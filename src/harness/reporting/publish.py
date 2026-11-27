"""Publish one report release to Yours through one function.

``publish(release, mode)`` takes a release, which names its package, its
publication target and its hooks, and an update mode: ``create`` a new site,
``update`` the bound site, or ``reuse`` a recorded publication. The target is one
schema for every track (``PublicationTarget``: host, site, expected version,
visibility and share link). A release records its outcome in one of three ways,
because tracks keep different authorities:

- ``ReceiptRelease`` records a submitted package in a private receipt; byte
  verification is a separate command.
- ``BoundTrack`` verifies the deployed bytes and public share, then advances the
  profile and attestation together.
- ``StagedUpdate`` resumes one staged update from a per-release journal, verifies
  it and lets the report bind its own target.

Existing receipts, journals and bindings keep their formats. Nothing here holds a
default site, and credentials stay behind ``PublicationClient``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from http.client import RemoteDisconnected
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote

from harness.record_io import json_bytes
from harness.reporting.publication import (
    PublicationClient,
    PublicationOperation,
    PublicationSnapshot,
    archive_inventory,
    atomic_write,
    bind_publication_files,
    binding_receipt_matches,
    export_manifest_files,
    export_original_url,
    multipart_archive,
    public_delivery_manifest,
    public_original_html,
    public_share_context,
    recover_binding,
    response_json,
    snapshot_publication,
    write_receipt,
)
from harness.reporting.publication_target import (
    PublicationTarget,
    publication_base,
    publication_document,
    read_target,
    require_publication_site,
    require_publication_version,
)
from harness.safety import reject_evidence_symlinks

STAGED_UPLOAD_THRESHOLD = 23 * 1024 * 1024
UPDATE_MODES = ("create", "update", "reuse")


class Release(Protocol):
    """A release the publisher can run; ``modes`` names the update modes it supports."""

    modes: tuple[str, ...]

    def publish(self, mode: str, transport) -> object: ...


def publish(release: Release, mode: str = "update", *, transport=None):
    """Publish one release in one update mode; tests pass a fake ``transport``."""
    if mode not in UPDATE_MODES:
        raise ValueError("Unknown publication mode")
    if mode not in release.modes:
        raise ValueError(f"Publication mode {mode!r} is not available for this release")
    return release.publish(mode, transport)


def updated_receipt(receipt, response):
    """Keep the receipt's concurrency baseline aligned with the activated version."""
    version = response.get('versionId')
    if not isinstance(version, str) or not version.startswith('ver_'):
        raise ValueError('Publication response omitted activated versionId')
    # Older receipts used ``version``. Keeping it alongside ``versionId`` leaves
    # two apparently authoritative baselines, one of which becomes stale here.
    current = {key: value for key, value in receipt.items() if key != 'version'}
    return {**current, 'versionId': version, 'last_update': response}


def _rate_limit_delay(error):
    header = error.headers.get('Retry-After', '') if error.headers else ''
    delay = 30.0
    if header:
        try:
            delay = float(header)
        except ValueError:
            try:
                delay = parsedate_to_datetime(header).timestamp() - time.time()
            except (TypeError, ValueError, OverflowError):
                pass
    if not math.isfinite(delay) or delay > 120:
        raise error
    return max(1.0, delay)


def put_file(request, path, data):
    """Retry only the identical idempotent staging PUT with bounded waits."""
    for attempt in range(4):
        try:
            return request(path, data, 'application/octet-stream', method='PUT')
        except HTTPError as error:
            if error.code >= 500:
                if attempt > 0:
                    raise
                delay=1.0
            elif error.code == 429:
                if attempt == 3:
                    raise
                delay = _rate_limit_delay(error)
            else:
                raise
            error.close()
            print(f'Upload failed with HTTP {error.code}; retrying the same file in {delay:.0f}s', flush=True)
            while delay > 0:
                interval = min(delay, 30)
                time.sleep(interval)
                delay -= interval
        except (TimeoutError, URLError, RemoteDisconnected):
            if attempt == 3:
                raise
            delay=min(2**attempt,4)
            print(f'Upload response unavailable; retrying the same file in {delay}s',flush=True)
            time.sleep(delay)


def _archive_entries(bundle):
    files = [info for info in bundle.infolist() if not info.is_dir()]
    names = [info.filename for info in files]
    if not files or len(set(names)) != len(names) or 'index.html' not in names:
        raise ValueError('Bundle must contain unique files and a root index.html')
    for name in names:
        if (name.startswith('/') or '\\' in name
                or any(part in ('', '..') or part.startswith('.')
                       or part.lower() == 'node_modules' for part in name.split('/'))
                or str(PurePosixPath(name)) != name):
            raise ValueError('Unsafe bundle path: ' + name)
    return files


def upload_large_archive(request, archive, slug=None, expected=None, title=None, *, max_workers=1, resume=False):
    """Upload unpacked bytes to staging and commit the full tree once."""
    if slug and not expected:
        raise ValueError('An existing site requires an exported version baseline')
    if not slug and not title:
        raise ValueError('A new site requires a title')

    def check_baseline():
        if slug:
            _, raw = request('/api/sites/' + quote(slug, safe=''))
            site = json.loads(raw)
            nested = site.get('site') if isinstance(site.get('site'), dict) else site
            observed = nested.get('currentVersionId') or (nested.get('version') or {}).get('id')
            if observed != expected:
                raise RuntimeError('Remote version changed; export and reconcile before publishing')

    check_baseline()
    with zipfile.ZipFile(archive) as bundle:
        files = _archive_entries(bundle)
        names = [info.filename for info in files]
        query = '?expected_version=' + quote(expected, safe='') if expected else ''
        payload = {'slug': slug} if slug else {'title': title}
        if slug and title is not None:
            payload['title'] = title
        _, raw = request('/api/uploads' + query, json.dumps(payload).encode(), 'application/json')
        session = json.loads(raw)['versionId']
        endpoint = '/api/uploads/' + quote(session, safe='')
        # The current service may choose the first uploaded HTML as the
        # version entry. Stage the root page before concurrent file uploads.
        retained = set()
        if resume:
            _, staged = request(endpoint)
            receipts = json.loads(staged).get('files', [])
            if any(item['relpath'] not in names for item in receipts):
                raise ValueError('Retained upload contains files outside this release')
            by_path = {item['relpath']: item for item in receipts}
            for info in files:
                receipt = by_path.get(info.filename, {})
                if (receipt.get('bytes') == info.file_size
                        and receipt.get('sha256') == hashlib.sha256(bundle.read(info)).hexdigest()):
                    retained.add(info.filename)
            print(f'Reusing {len(retained)}/{len(files)} verified uploaded files', flush=True)
        entry = next(info for info in files if info.filename == 'index.html')
        if entry.filename not in retained:
            put_file(request, endpoint + '/files/index.html', bundle.read(entry))
        remaining = [info for info in files
                     if info.filename != 'index.html' and info.filename not in retained]
        if max_workers > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                pending = {
                    pool.submit(put_file, request,
                                endpoint + '/files/' + quote(info.filename, safe='/'),
                                bundle.read(info)): info.filename
                    for info in remaining
                }
                for index, future in enumerate(as_completed(pending), 2):
                    future.result()
                    if index % 100 == 0 or index == len(files):
                        print(f'Uploaded {index}/{len(files)} files', flush=True)
        else:
            for index, info in enumerate(remaining, 2):
                put_file(request, endpoint + '/files/' + quote(info.filename, safe='/'),
                         bundle.read(info))
                print(f'Uploaded {index}/{len(files)} files', flush=True)
        # The manual only guarantees atomic expected_version enforcement on
        # /versions and /edit. Also recheck before chunk commit; this read is
        # explicitly not claimed to be an atomic server-side lock.
        for attempt in range(4):
            check_baseline()
            try:
                _, raw = request(endpoint + '/commit' + query, b'{}', 'application/json')
                break
            except HTTPError as error:
                if error.code != 429 or attempt == 3:
                    raise
                delay = _rate_limit_delay(error)
                error.close()
                print(f'Commit rate limited; retrying the same operation in {delay:g}s', flush=True)
                while delay > 0:
                    interval = min(delay, 30)
                    time.sleep(interval)
                    delay -= interval
        if slug and json.loads(raw).get('slug', slug) != slug:
            raise RuntimeError('Publication returned an unexpected site')
        return raw


def site_version(site: dict) -> str:
    """Read the active version from either shape of the site metadata response."""
    nested = site.get("site") if isinstance(site.get("site"), dict) else site
    version = str(nested.get("currentVersionId") or (nested.get("version") or {}).get("id") or "")
    if not version.startswith("ver_"):
        raise RuntimeError("Yours site metadata omitted current version")
    return version


def verify_export(
    request: Callable, slug: str, archive: Path, *, public_base: str | None = None,
) -> None:
    """Verify ZIP exports or version-pinned original files from manifest exports."""
    site_path = "/api/sites/" + quote(slug, safe="")
    status, exported = request(site_path + "/export")
    if status != 200:
        response_json(status, exported, operation="site export")
    wanted = archive_inventory(archive)
    if zipfile.is_zipfile(BytesIO(exported)):
        if archive_inventory(exported) != wanted:
            raise RuntimeError("deployed Yours file tree differs from package")
        return
    status, raw = request(site_path)
    version = site_version(response_json(status, raw, operation="site metadata"))
    if not exported.lstrip().startswith(b"{"):
        archive_inventory(exported)  # Preserve malformed ZIP rejection for legacy exports.
    manifest = response_json(200, exported, operation="site export")
    files = export_manifest_files(manifest, version)
    if {item["path"] for item in files} != set(wanted):
        raise RuntimeError("deployed Yours file tree differs from package")

    delivery = None
    if public_base is not None:
        endpoint = public_base.rstrip("/") + "/api/preview/" + quote(slug, safe="")
        try:
            with urllib.request.urlopen(endpoint, timeout=180) as response:
                served = response.read()
        except HTTPError as error:
            if error.code not in (401, 403, 404):
                raise
            error.close()
        else:
            delivery = public_delivery_manifest(
                served, base=public_base, version=version, path="index.html",
            )
            if delivery is not None:
                if {item["path"] for item in delivery["files"]} != set(wanted):
                    raise RuntimeError("Public delivery file tree differs from package")
                delivery = {item["path"]: item for item in delivery["files"]}

    def verify_original(entry):
        path = entry["path"]
        if delivery is None:
            query = urllib.parse.urlencode({"version": version, "file": path})
            status, raw = request(site_path + "/download?" + query)
            if status != 200:
                response_json(status, raw, operation="original download")
            digest, count = hashlib.sha256(raw), len(raw)
            if digest.hexdigest() == wanted[path]:
                if count != entry["size"]:
                    raise RuntimeError("Original file size differs from package: " + path)
                return
            descriptor = response_json(200, raw, operation="original download descriptor")
        else:
            descriptor = {"assembled": False, "versionId": version,
                          "filename": path, "url": delivery[path]["url"]}
        address = export_original_url(descriptor, version, path)
        digest, count = hashlib.sha256(), 0
        # Signed storage URLs never receive the publication credential.
        with urllib.request.urlopen(urllib.request.Request(address), timeout=180) as response:
            while chunk := response.read(min(1024 * 1024, entry["size"] - count + 1)):
                count += len(chunk)
                if count > entry["size"]:
                    raise ValueError("Original file exceeds export size: " + path)
                digest.update(chunk)
        if count != entry["size"] or digest.hexdigest() != wanted[path]:
            raise RuntimeError("deployed Yours file bytes differ from package: " + path)

    with ThreadPoolExecutor(max_workers=4) as workers:
        for index, _ in enumerate(workers.map(verify_original, files), 1):
            if index % 100 == 0 or index == len(files):
                print(f"Verified {index}/{len(files)} deployed files", flush=True)
    status, raw = request(site_path)
    if site_version(response_json(status, raw, operation="site metadata after verification")) != version:
        raise RuntimeError("Remote version changed during export verification")

# Receipt lifecycle


@dataclass(frozen=True)
class ReceiptRelease:
    """A package submitted to the target its profile names, recorded in a private receipt."""

    profile: Path
    archive: Path
    source: Path
    receipt: Path
    journal: Path
    namespace: str
    upload_name: str
    client: PublicationClient
    package_check: Callable
    title: str
    # A requested target; each field set here must agree with the bound profile.
    target: PublicationTarget = field(default_factory=PublicationTarget)
    inputs: tuple = ()
    check_reuse: bool = True
    check_target: Callable | None = None
    check_delivery: Callable | None = None
    modes = UPDATE_MODES

    def publish(self, mode, transport):
        return _publish_receipt(self, mode, transport=transport)


def activated_version(result):
    version = result.get("versionId") or result.get("version")
    if isinstance(version, dict):
        version = version.get("id")
    if not isinstance(version, str) or not version.startswith("ver_"):
        raise ValueError("Publication receipt omitted an activated version")
    return version


def release_multipart(archive, title, digest, upload_name):
    return multipart_archive(
        archive,
        title,
        boundary_prefix="----" + upload_name,
        filename=upload_name + ".zip",
        boundary_seed=(digest + "|" + title).encode(),
    )


def _receipt_identity(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("Publication receipt must be a regular file")
    return path.read_bytes() if path.exists() else None


def _publish_receipt(config: ReceiptRelease, mode: str, *, transport=None):
    """Upload or finish a receipt; remote byte verification is a separate command."""
    requested = config.target
    protected = {config.profile.resolve(), config.archive.resolve(),
                 *(Path(path).resolve() for path in config.inputs)}
    if config.receipt.resolve() in protected or config.journal.resolve() in protected:
        raise ValueError("Publication receipts and journals must not replace package inputs")
    reject_evidence_symlinks(config.profile)
    profile_bytes = config.profile.read_bytes()
    profile = publication_document(json.loads(profile_bytes))
    base = publication_base(config.profile, requested.base_url, document=profile)
    if config.check_target is not None:
        config.check_target(profile, base)
    previous_receipt = _receipt_identity(config.receipt)
    result = json.loads(previous_receipt) if previous_receipt is not None else {}
    if not isinstance(result, dict):
        raise ValueError("Publication receipt must be a JSON object")
    if config.receipt.resolve() == config.journal.resolve():
        raise ValueError("Publication authority, receipt and operation journal must be distinct")
    # The release owns its title and the receipt its share link; a requested
    # value must name the same one rather than be dropped.
    if requested.title is not None and requested.title != config.title:
        raise ValueError("Requested publication title differs from the release title")
    if requested.share_url is not None and requested.share_url != (
            result.get("share_url") or (profile.get("publication") or {}).get("share_url")):
        raise ValueError("Requested share link differs from the saved publication")
    if requested.site is not None:
        if mode == "create":
            raise ValueError("A new site is named by the service; do not request a site with --create")
        if requested.site != profile.get("artifact_hub_site"):
            raise ValueError("Requested publication site differs from the bound profile")
    if mode == "create":
        if profile.get("artifact_hub_site") or profile.get("artifact_hub_expected_version"):
            raise ValueError("--create requires an unbound publication profile")
    else:
        if previous_receipt is None:
            raise ValueError("Current report requires its existing site receipt")
        require_publication_site(result, config.profile, base, document=profile)
    expected = (
        require_publication_version(config.profile, requested.expected_version, document=profile)
        if mode == "update"
        else None
    )

    def release_guard():
        pass

    def local_guard():
        release_guard()
        reject_evidence_symlinks(config.profile)
        if config.profile.read_bytes() != profile_bytes:
            raise ValueError("Publication profile changed during submission")
        if _receipt_identity(config.receipt) != previous_receipt:
            raise ValueError("Publication receipt changed during submission")

    def send(path, data=None, content_type=None, *, method=None, extra_headers=None):
        local_guard()
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Publication request must stay on the configured service")
        if transport is not None:
            return transport(path, data, content_type, method=method, extra_headers=extra_headers)
        return config.client.request(
            base, path, data, content_type, method=method, extra_headers=extra_headers
        )[:2]

    def save(value):
        nonlocal previous_receipt
        local_guard()
        previous_receipt = write_receipt(config.receipt, value)

    def finish(value):
        version = activated_version(value)
        saved_url = value.get('share_url') or (profile.get('publication') or {}).get('share_url')
        if saved_url:
            target = urllib.parse.urlsplit(saved_url)
            authority = urllib.parse.urlsplit(base)
            prefix = authority.path.rstrip('/')
            canonical = prefix+'/s/'+quote(value['slug'], safe='')
            legacy = re.fullmatch(re.escape(prefix+'/v/')+r'[A-Za-z0-9_-]+', target.path)
            if (target.scheme != authority.scheme or target.netloc != authority.netloc
                    or target.query or target.fragment or not (target.path == canonical or legacy)):
                raise ValueError('Saved report URL differs from the configured service')
        if mode == 'reuse' and requested.visibility is None and value.get('share_url'):
            return {'slug': value['slug'], 'versionId': version, 'url': saved_url,
                    **({'visibility': value['visibility']} if value.get('visibility') else {})}
        slug = quote(value['slug'], safe='')
        path = '/api/sites/'+slug+'/sharing'
        status, raw = send(path)
        sharing = response_json(status, raw, operation='sharing inspection')
        visibility = requested.visibility or sharing.get('visibility')
        if visibility not in {'private', 'unlisted', 'public'}:
            raise ValueError('Publication did not return its current access state')
        if requested.visibility is not None and sharing.get('visibility') != visibility:
            status, raw = send(path, json_bytes({'visibility': visibility}),
                               'application/json', method='PUT')
            sharing = response_json(status, raw, operation='sharing update')
        if sharing.get('visibility') != visibility or sharing.get('moderation'):
            raise ValueError('Requested sharing is not active; inspect the saved publication')
        value.update(share_url=(saved_url if saved_url and requested.visibility is None else base+'/s/'+slug), visibility=visibility,
                     publication_pending=False)
        save(value)
        return {'slug': value['slug'], 'versionId': version,
                'url': value['share_url'], 'visibility': visibility}

    if mode == "reuse":
        if not config.check_reuse:
            return finish(result)
        with snapshot_publication(
            config.archive, inputs=[config.profile, config.source, *config.inputs]
        ) as release:
            release_guard = release.check
            config.package_check(release.archive, config.source)
            release.check()
            summary = finish(result)
            release.check()
            return summary
    for path in (config.archive, config.receipt, config.journal):
        if path.resolve().is_relative_to(config.source.resolve()):
            raise ValueError("Publication outputs must stay outside the source report")
    with snapshot_publication(
        config.archive, inputs=[config.profile, config.source, *config.inputs]
    ) as release:
        release_guard = release.check
        config.package_check(release.archive, config.source)
        release.check()
        if config.check_delivery is not None:
            config.check_delivery(release.archive, profile, base)
            release.check()
        original_send = send

        def guarded_send(*args, **kwargs):
            if '/commit' in args[0] or '/versions' in args[0]:
                release.verify()
            return original_send(*args, **kwargs)

        send = guarded_send
        identity = {
            "schema": config.namespace + "_publication_operation/1",
            "base_url": base,
            "archive_sha256": release.sha256,
            "title": config.title,
            "kind": mode,
            "slug": None if mode == "create" else result["slug"],
            "expected_version": expected,
        }
        operations = PublicationOperation(config.journal, config.namespace + "-")
        pending, resumed = operations.pending(
            identity, allow_create=mode != "create" or previous_receipt is None
        )
        already_submitted = (
            result.get("operation_key") == pending["key"]
            and result.get("archive_sha256") == release.sha256
            and result.get("base_url") == base
        )
        if mode == "create" and previous_receipt is not None and not already_submitted:
            raise ValueError("--create requires a new receipt or its matching pending operation")
        if not already_submitted:
            recovered = (
                operations.completed(pending["key"], request=send, decode=response_json)
                if resumed
                else None
            )
            if recovered is not None:
                status, raw = recovered
            elif release.archive.stat().st_size > STAGED_UPLOAD_THRESHOLD:

                def staged_send(path, data=None, content_type=None, *, method=None):
                    headers = None
                    if data is not None and method is None:
                        key = pending["key"] if "/commit" in path else pending["key"] + ":start"
                        headers = {"Idempotency-Key": key}
                    return send(path, data, content_type, method=method, extra_headers=headers)

                raw = upload_large_archive(
                    staged_send, release.archive, identity["slug"], expected, title=config.title, max_workers=4
                )
                status = 200
            else:
                body, content_type = release_multipart(
                    release.archive, config.title, release.sha256, config.upload_name
                )
                endpoint = (
                    "/api/sites"
                    if mode == "create"
                    else "/api/sites/"
                    + quote(result["slug"], safe="")
                    + "/versions?"
                    + urllib.parse.urlencode({"expected_version": expected})
                )
                status, raw = send(
                    endpoint, body, content_type, extra_headers={"Idempotency-Key": pending["key"]}
                )
            response = response_json(status, raw, operation="package submission")
            if mode == "update":
                if response.get("slug", result["slug"]) != result["slug"]:
                    raise ValueError("Publication returned an unexpected site")
                result = updated_receipt(result, response)
            else:
                result = response
                if not isinstance(result.get("slug"), str) or not result["slug"]:
                    raise ValueError("Publication did not return a site slug")
            activated_version(result)
            for key in ("verified", "verification", "verified_at"):
                result.pop(key, None)
            result.update(
                base_url=base,
                archive_sha256=release.sha256,
                publication_pending=True,
                operation_key=pending["key"],
                submission_state="submitted_unverified",
            )
            save(result)
        summary = finish(result)
        release.verify()
        operations.clear(identity)
        return summary


# Bound lifecycle


@dataclass(frozen=True)
class BoundTrack:
    """A report whose verified publication advances its profile and attestation.

    The target is the profile's ``publication`` object. Upload names, binding
    schemas and clients are data; ``validate_package`` and ``check_preview`` are
    the track's hooks.
    """

    label: str
    default_title: str
    archive: Path
    attestation: Path
    profile: Path
    receipt: Path
    operation_journal: Path
    binding_journal: Path
    inputs: tuple
    operation_prefix: str
    operation_schema: str
    binding_schema: str
    version_field: str
    client: PublicationClient
    upload_boundary: str  # stable multipart names; a retried operation replays the same bytes
    upload_filename: str
    validate_package: Callable
    check_preview: Callable  # (status, body)
    public_client: PublicationClient | None = None  # unauthenticated share checks; default ``client``
    legacy_binding_schema: str | None = None
    binding_state: str | None = None
    receipt_required: bool = False
    update_title: bool = False
    staged_key_prefix: str | None = None
    modes = ("create", "update")

    def publish(self, mode, transport):
        return _publish_bound_release(self, create=mode == "create", transport=transport)

    def multipart(self, title: str, archive: Path | None = None) -> tuple[bytes, str]:
        return multipart_archive(archive or self.archive, title,
                                 boundary_prefix=self.upload_boundary, filename=self.upload_filename)

    def recover_binding(self) -> None:
        recover_binding(self.profile, self.attestation, self.binding_journal,
                        schema=self.binding_schema, legacy_schema=self.legacy_binding_schema,
                        writer=atomic_write)

    def bind(self, profile: dict, *, slug: str, share_url: str, version: str,
             release: PublicationSnapshot | None = None) -> None:
        """Advance the profile's target and the attestation together."""
        if not version.startswith("ver_"):
            raise ValueError("publication response omitted a valid version id")
        if json.loads(self.profile.read_bytes()) != profile:
            raise ValueError("publication profile changed before binding")
        publication = {"site_slug": slug, "share_url": share_url, "expected_version": version}
        if self.binding_state is not None:
            publication["state"] = self.binding_state
        bind_publication_files(
            self.profile, self.attestation, self.binding_journal, publication=publication,
            schema=self.binding_schema, expected=release.sources if release is not None else None,
            writer=atomic_write,
        )


def operation_identity(
    schema: str, *, base: str, title: str, archive_sha256: str, slug: str = "",
    expected_version: str = "",
) -> dict:
    return {
        "schema": schema,
        "base_url": base,
        "archive_sha256": archive_sha256,
        "title": title,
        "kind": "version" if slug or expected_version else "create",
        "slug": slug,
        "expected_version": expected_version,
    }


def pending_created_site(receipt_path: Path, base: str, archive_sha256: str) -> str:
    """Name a site created by an interrupted run, only for this exact release."""
    if not receipt_path.is_file():
        return ""
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("verified"):
        return ""
    if (
        receipt.get("base_url") != base
        or receipt.get("archive_sha256") != archive_sha256
        or not isinstance(receipt.get("slug"), str)
        or not receipt["slug"]
    ):
        raise ValueError("pending site receipt differs from the current release package")
    return receipt["slug"]


def staged_release_upload(
    request: Callable, archive: Path, *, title: str, slug: str | None,
    expected_version: str | None, identity: dict, key_prefix: str,
) -> bytes:
    """Upload a large archive as validated files and activate it in one commit."""

    def staged_request(path, data=None, content_type=None, *, method=None):
        headers = None
        if (
            data is not None
            and method is None
            and (path.startswith("/api/uploads?") or "/commit" in path)
        ):
            seed = json.dumps(identity, sort_keys=True, separators=(",", ":")) + "|" + path
            headers = {"Idempotency-Key": key_prefix + hashlib.sha256(seed.encode()).hexdigest()}
        status, raw, *response_headers = request(
            path, data, content_type, method=method, extra_headers=headers
        )
        if not 200 <= status < 300:
            raise HTTPError(
                path,
                status,
                raw.decode("utf-8", errors="replace")[:500],
                response_headers[0] if response_headers else {},
                BytesIO(raw),
            )
        return status, raw

    return upload_large_archive(
        staged_request, archive, slug, expected_version, title=title, max_workers=4,
    )


def _publish_bound_release(track: BoundTrack, *, create: bool, transport=None) -> None:
    track.recover_binding()
    with snapshot_publication(track.archive, inputs=track.inputs) as release:
        track.validate_package()
        release.check()
        _publish_bound(track, release, create=create, transport=transport)


def _publish_bound(track: BoundTrack, release: PublicationSnapshot, *, create: bool,
                   transport=None) -> None:
    profile = json.loads(track.profile.read_text(encoding="utf-8"))
    target = read_target({"publication": profile.get("publication") or {}})
    base = str(target.base_url or "").rstrip("/")
    bound_slug = target.site or ""
    expected_version = target.expected_version or ""
    if not base.startswith("https://"):
        raise ValueError(f"current {track.label} profile has no valid publication base")
    if target.visibility not in (None, "public"):
        # This lifecycle verifies the release through its public share; it cannot withdraw it.
        raise ValueError(f"{track.label} publication only supports public sharing; "
                         "change the site's access separately before publishing")
    title = str(target.title or track.default_title)
    public_client = track.public_client or track.client

    def request(base, path, *args, **kwargs):
        if transport is not None:
            return transport(base, path, *args, **kwargs)
        return track.client.request(base, path, *args, **kwargs)

    def public_request(base, path, **kwargs):
        if transport is not None:
            return transport(base, path, authenticated=False, **kwargs)
        return public_client.request(base, path, authenticated=False, **kwargs)
    operations = PublicationOperation(track.operation_journal, track.operation_prefix)
    identity = operation_identity(
        track.operation_schema, base=base, title=title, archive_sha256=release.sha256,
        slug=bound_slug, expected_version=expected_version,
    )

    def raw_send(path, *args, **kwargs):
        release.check()
        return request(base, path, *args, **kwargs)

    def send(path, *args, **kwargs):
        return raw_send(path, *args, **kwargs)[:2]

    def upload(path: str, *, slug: str | None, expected: str | None, allow_create: bool):
        if (track.staged_key_prefix is not None
                and release.archive.stat().st_size > STAGED_UPLOAD_THRESHOLD):
            if slug is None and not allow_create:
                raise ValueError("no bound site or matching pending creation operation")
            return 200, staged_release_upload(
                raw_send, release.archive, title=title, slug=slug, expected_version=expected,
                identity=identity, key_prefix=track.staged_key_prefix,
            )
        payload, content_type = track.multipart(title, release.archive)
        return operations.upload(
            path, payload, content_type, identity, allow_create=allow_create,
            request=send, decode=response_json, guard=release.check,
        )

    resume_binding = binding_receipt_matches(
        track.receipt, base=base, archive_sha256=release.sha256, slug=bound_slug,
        version=expected_version, version_field=track.version_field,
    )
    verified_version = None
    if bound_slug or expected_version:
        if create and not resume_binding:
            raise ValueError(f"{track.label} report is already bound; update it without --create")
        if not bound_slug or not expected_version.startswith("ver_"):
            raise ValueError(f"current {track.label} profile has no complete publication binding")
        if track.receipt_required and not track.receipt.exists():
            raise ValueError(f"bound {track.label} site receipt is missing")
        result = json.loads(track.receipt.read_bytes()) if track.receipt.exists() else {}
        if result and result.get("slug") != bound_slug:
            raise ValueError(f"site receipt differs from the bound {track.label} site")
        slug = bound_slug
        status, raw = send(f"/api/sites/{quote(slug)}")
        remote_version = site_version(response_json(status, raw, operation="site lookup"))
        if remote_version != expected_version or resume_binding:
            # A previous upload may have committed before a post-upload check failed.
            # Resume only when the exported tree proves that the remote version is
            # byte-identical to the current package.
            verify_export(send, slug, release.archive, public_base=base)
            verified_version = remote_version
        else:
            query = urllib.parse.urlencode({"expected_version": expected_version})
            status, raw = upload(
                f"/api/sites/{quote(slug)}/versions?{query}",
                slug=slug, expected=expected_version, allow_create=True,
            )
            result.update(response_json(status, raw, operation="site version upload"))
        share_url = str(target.share_url or result.get("share_url") or "")
    else:
        slug = pending_created_site(track.receipt, base, release.sha256)
        if slug:
            result = json.loads(track.receipt.read_bytes())
            if track.operation_journal.exists():
                operations.pending(identity, allow_create=False)
            verify_export(send, slug, release.archive, public_base=base)
        else:
            status, raw = upload("/api/sites", slug=None, expected=None, allow_create=create)
            result = response_json(status, raw, operation="site creation")
            slug = str(result.get("slug") or "")
            if not slug:
                raise RuntimeError("site creation response omitted slug")
            result.update(base_url=base, archive_sha256=release.sha256, verified=False)
            write_receipt(track.receipt, result)
        share_url = str(result.get("share_url") or "")
    site_path = f"/api/sites/{quote(slug)}"
    if not share_url:
        status, raw = send(
            site_path + "/shares", json.dumps({"policy": "public"}).encode(),
            "application/json",
        )
        share_url = str(response_json(status, raw, operation="public share creation").get("url") or "")
        if not share_url.startswith("https://"):
            raise RuntimeError("share creation response omitted public URL")
    status, raw = send(site_path)
    version = site_version(response_json(status, raw, operation="site lookup after upload"))
    if track.update_title:
        status, raw = send(site_path, json.dumps({"title": title}).encode(), "application/json",
                           method="PATCH")
        if not 200 <= status < 300:
            response_json(status, raw, operation="site title update")
        result["title"] = title
    if verified_version != version:
        verify_export(send, slug, release.archive, public_base=base)
    share_path, share_token = public_share_context(base, share_url)
    release.check()
    status, _ = public_request(base, share_path)[:2]
    if status != 200:
        raise RuntimeError(f"public report returned HTTP {status}")
    release.check()
    status, body = public_request(
        base, f"/api/preview/{quote(slug)}", extra_headers={"X-Artifact-Share": share_token} if share_token else None
    )[:2]
    delivered = public_original_html(body, base=base, version=version, path="index.html")
    if delivered is not None:
        if hashlib.sha256(delivered).hexdigest() != archive_inventory(release.archive)["index.html"]:
            raise ValueError("Public delivery entry differs from the validated package")
        body = delivered
    track.check_preview(status, body)
    release.check()
    track.validate_package()
    release.check()
    status, raw = send(site_path)
    if site_version(response_json(status, raw, operation="final site lookup")) != version:
        raise ValueError("remote version changed before publication binding")
    settled = {key: value for key, value in result.items()
               if key not in {"phase", "version", "versionId", "version_id", "currentVersionId"}}
    settled.update({"base_url": base, "slug": slug, "share_url": share_url,
                    track.version_field: version, "archive_sha256": release.sha256})
    write_receipt(track.receipt, {**settled, "verified": False, "phase": "binding"})
    operations.clear(identity)
    track.bind(profile, slug=slug, share_url=share_url, version=version, release=release)
    write_receipt(track.receipt, {**settled, "verified": True})
    print(json.dumps({"slug": slug, "url": share_url, "version": version}, ensure_ascii=False))


# Staged update lifecycle


@dataclass(frozen=True)
class StagedUpdate:
    """One release of a bound site whose flat profile the report advances itself."""

    label: str
    prefix: str
    client: PublicationClient
    target: PublicationTarget  # base, site, expected version and, when held, the share link
    title: str
    archive: Path
    archive_sha256: str
    inputs: tuple
    work: Path
    guard: Callable
    bind: Callable  # (version) inside the frozen release
    allow_inline: bool = False
    inline_limit_bytes: int | None = None
    modes = ("update",)

    def publish(self, mode, transport):
        return _publish_staged_update(self, transport=transport)

    @property
    def base(self) -> str:
        return self.target.base_url

    @property
    def slug(self) -> str:
        return self.target.site

    @property
    def expected_version(self) -> str:
        return self.target.expected_version

    @property
    def share_path(self) -> str:
        if self.target.share_url:
            return public_share_context(self.base, self.target.share_url)[0]
        return "/s/" + self.slug


def _publish_staged_update(update: StagedUpdate, *, transport=None) -> str:
    """Resume the same staged update and verify every deployed byte before binding it."""
    if not update.base or not update.slug or not update.expected_version:
        raise ValueError(f"{update.label} publication requires a bound site and version")
    if update.target.visibility not in (None, "public"):
        # A staged update verifies its public share and cannot change access; refuse before
        # writing a journal or sending anything so a withdrawn report is never left public.
        raise ValueError(f"{update.label} publication only supports public sharing; "
                         "change the site's access separately before publishing")
    inline_limit = update.inline_limit_bytes
    if inline_limit is not None and (type(inline_limit) is not int or inline_limit <= 0):
        raise ValueError('Inline upload limit must be a positive byte count')
    if inline_limit is None:
        inline_limit = STAGED_UPLOAD_THRESHOLD
    label, slug = update.label, update.slug
    identity = {'base_url': update.base, 'slug': slug, 'archive_sha256': update.archive_sha256}
    # The release identifies the operation; its mutable live version is a guard.
    operation_hash = hashlib.sha256(json_bytes(identity)).hexdigest()
    journal = update.work / f'{update.prefix}-update-{operation_hash[:24]}.json'
    reject_evidence_symlinks(journal)
    resumed = journal.exists()
    operation = (json.loads(journal.read_bytes()) if resumed
                 else {**identity, 'expected_version': update.expected_version})
    if any(operation.get(key) != value for key, value in identity.items()):
        raise ValueError(f'{label} publication journal targets another release')
    if update.expected_version not in {operation.get('expected_version'), operation.get('versionId')}:
        raise ValueError(f'{label} publication version differs from its pending operation')
    expected = operation['expected_version']

    def save():
        atomic_write(journal, json_bytes(operation))
        journal.chmod(0o600)

    save()
    with snapshot_publication(update.archive, inputs=update.inputs) as release:
        with zipfile.ZipFile(release.archive) as bundle:
            _archive_entries(bundle)
        if 'transport' not in operation:
            # Existing journals may already own staged bytes. Never switch them.
            operation['transport'] = ('inline' if update.allow_inline and not resumed and
                                      release.archive.stat().st_size <= inline_limit
                                      else 'staged')
            save()
        if operation['transport'] not in {'inline', 'staged'}:
            raise ValueError(f'{label} publication transport changed')
        commit_key = f'{update.prefix}-{"inline" if operation["transport"] == "inline" else "commit"}-{operation_hash[:32]}'
        def request(path, data=None, content_type=None, auth=True, method=None):
            release.check()
            update.guard()
            if not path.startswith('/') or path.startswith('//'):
                raise ValueError(f'{label} request must stay on its configured service')
            extra_headers = {}
            if path.startswith('/api/uploads') and method != 'PUT' and data is not None:
                phase = 'commit' if '/commit?' in path else 'start'
                extra_headers['Idempotency-Key'] = f'{update.prefix}-{phase}-{operation_hash[:32]}'
            elif path.startswith('/api/sites/') and '/versions?' in path and data is not None:
                extra_headers['Idempotency-Key'] = commit_key
            read_only = data is None and method in (None, 'GET', 'HEAD')
            for attempt in range(4):
                try:
                    if transport is not None:
                        # Synthetic transports never load a real account credential.
                        headers = update.client.headers(update.base, content_type, authenticated=False,
                                                        extra_headers=extra_headers)
                        status, raw = transport(path, data, content_type, auth=auth,
                                                method=method, headers=headers)
                        response_headers = {}
                    else:
                        status, raw, response_headers = update.client.request(
                            update.base, path, data, content_type, method=method,
                            authenticated=auth, extra_headers=extra_headers)
                    if status == 429 and read_only:
                        raise HTTPError(update.base + path, status, 'rate limited',
                                        response_headers, BytesIO(raw))
                    break
                except HTTPError as error:
                    if not read_only or error.code != 429 or attempt == 3:
                        raise
                    delay = _rate_limit_delay(error)
                    error.close()
                    print(f'{label} verification rate limited; retrying the same read '
                          f'in {delay:g}s', flush=True)
                    while delay > 0:
                        interval = min(delay, 30)
                        time.sleep(interval)
                        delay -= interval
                    release.check()
                    update.guard()
            release.check()
            if status == 404 and path.startswith('/api/operations/'):
                return status, raw
            if not 200 <= status < 300:
                raise RuntimeError(f'{label} publication HTTP {status}: {path}')
            return status, raw

        site_path = '/api/sites/' + quote(slug, safe='')
        started = time.perf_counter()
        print(f'Preparing {label} remote version ({operation["transport"]})...', flush=True)
        _, raw = request(site_path)
        remote = json.loads(raw)
        version = operation.get('versionId')
        if version is None:
            if remote['version']['id'] != expected:
                recovered = PublicationOperation(journal, update.prefix + '-').completed(
                    commit_key, request=request, decode=response_json,
                )
                if recovered is None:
                    raise ValueError(f'Remote {label} version changed; reconcile before updating')
                response = response_json(*recovered, operation=f'{label} commit recovery')
            else:
                recovered = None
                if resumed and operation['transport'] == 'inline':
                    recovered = PublicationOperation(journal, update.prefix + '-').completed(
                        commit_key, request=request, decode=response_json)
                if recovered is not None:
                    response = response_json(*recovered, operation=f'{label} inline recovery')
                elif operation['transport'] == 'inline':
                    body, content_type = multipart_archive(
                        release.archive, update.title, boundary_prefix=update.prefix + '-',
                        filename='report.zip')
                    response = response_json(*request(
                        site_path + '/versions?expected_version=' + quote(expected, safe=''),
                        body, content_type), operation=f'{label} inline upload')
                else:
                    response = json.loads(upload_large_archive(request, release.archive, slug, expected, max_workers=4, resume=resumed))
            version = response.get('versionId')
            if (response.get('slug', slug) != slug or not isinstance(version, str)
                    or not version.startswith('ver_')):
                raise ValueError(f'{label} upload returned another site or an invalid version')
            operation['versionId'] = version
            save()
        elif not isinstance(version, str) or not version.startswith('ver_'):
            raise ValueError(f'{label} journal has an invalid activated version')
        print(f'{label} remote version ready in {time.perf_counter() - started:.1f}s', flush=True)
        started = time.perf_counter()
        print(f'Verifying deployed {label} files...', flush=True)
        _, raw = request(site_path)
        published = json.loads(raw)
        if published['version']['id'] != version or published['version']['entry'] != 'index.html':
            raise ValueError(f'Published {label} site does not bind the activated home page')
        try:
            verify_export(request, slug, release.archive, public_base=update.base)
        except RuntimeError as error:
            raise ValueError(f'Deployed {label} bytes differ from the validated package') from error
        _, shared = request(update.share_path, auth=False)
        if not shared:
            raise ValueError(f'{label} public share page is empty')
        request(site_path, json.dumps({'title': update.title}, ensure_ascii=False).encode(),
                'application/json', method='PATCH')
        release.check()
        update.bind(version)
        print(f'{label} deployed files verified in {time.perf_counter() - started:.1f}s', flush=True)
    operation['verified'] = True
    save()
    return version
