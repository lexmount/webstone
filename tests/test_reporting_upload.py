"""Publication freezes uploads and refuses a changed local release authority."""
import json
import urllib.request
import zipfile
from types import SimpleNamespace

import pytest

from harness.browser_use import report_publish as autonomous
from harness.record_io import file_sha256
from harness.reporting.publication import snapshot_publication
from harness.reporting.publish import staged_release_upload, upload_large_archive
from harness.trajectory_replay import webarena_report_publish as replay


@pytest.fixture(autouse=True)
def private_binding_journals(monkeypatch, tmp_path):
    monkeypatch.setattr(autonomous, 'BINDING_JOURNAL', tmp_path / 'autonomous-binding.json')
    monkeypatch.setattr(replay, 'BINDING_JOURNAL', tmp_path / 'replay-binding.json')
    monkeypatch.setattr(autonomous, 'OPERATION_JOURNAL', tmp_path / 'autonomous-operation.json')
    monkeypatch.setattr(replay, 'OPERATION_JOURNAL', tmp_path / 'replay-operation.json')


def test_new_site_upload_without_a_title_stops_before_any_request(tmp_path):
    archive = tmp_path / 'archive.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('index.html', 'Frozen release')
    with pytest.raises(ValueError, match='requires a title'):
        upload_large_archive(lambda *args, **kwargs: pytest.fail('sent a request'), archive)


def test_private_archive_is_byte_exact_and_removed_on_error(tmp_path):
    archive = tmp_path / 'archive.zip'
    archive.write_bytes(b'frozen content')
    profile = tmp_path / 'profile.json'
    profile.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='inputs changed'):
        with snapshot_publication(archive, inputs=[profile]) as release:
            frozen = release.archive
            assert frozen.read_bytes() == archive.read_bytes()
            archive.write_bytes(b'changed')
            assert frozen.read_bytes() == b'frozen content'
            release.check()
    assert not frozen.exists()


@pytest.mark.parametrize('track', ['browser-use', 'replay'])
@pytest.mark.parametrize('damage', ['archive', 'profile', 'attestation'])
def test_lookup_input_change_stops_before_version_upload(tmp_path, monkeypatch, track, damage):
    owner = autonomous if track == 'browser-use' else replay
    files = {}
    for key in ('ARCHIVE', 'ATTESTATION', 'PROFILE', 'REVIEW', 'RECEIPT'):
        path = tmp_path / (key.lower() + '.json')
        path.write_text('{}', encoding='utf-8')
        files[key] = path
        if hasattr(owner, key):
            monkeypatch.setattr(owner, key, path)
    with zipfile.ZipFile(files['ARCHIVE'], 'w') as bundle:
        bundle.writestr('index.html', 'Frozen release')
    publication = {'base_url': 'https://example.invalid', 'site_slug': 'site-1',
                   'expected_version': 'ver_old', 'share_url': 'https://example.invalid/v/token'}
    files['PROFILE'].write_text(json.dumps({'publication': publication}), encoding='utf-8')
    files['RECEIPT'].write_text('{"slug":"site-1"}', encoding='utf-8')
    monkeypatch.setattr(owner, 'validate_package', lambda: {})
    if track == 'replay':
        monkeypatch.setattr(owner, 'BINDING_JOURNAL', tmp_path / 'binding.json')
        monkeypatch.setattr(owner, 'OPERATION_JOURNAL', tmp_path / 'operation.json')
    calls = []
    def request(*args, **kwargs):
        path = args[1]
        calls.append(path)
        assert path == '/api/sites/site-1'
        files[damage.upper()].write_text('Changed during site lookup', encoding='utf-8')
        result = (200, b'{"site":{"currentVersionId":"ver_old"}}')
        return (*result, {}) if track == 'replay' else result
    monkeypatch.setattr(owner, 'CLIENT', SimpleNamespace(request=request))
    with pytest.raises(ValueError, match='inputs changed'):
        owner.main(create=False)
    assert calls == ['/api/sites/site-1']
    assert json.loads(files['RECEIPT'].read_text(encoding='utf-8')) == {'slug': 'site-1'}


def test_staged_upload_stops_before_commit_when_original_changes(tmp_path, monkeypatch):
    archive = tmp_path / 'archive.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('index.html', 'Frozen root')
        bundle.writestr('assets/app.js', 'Frozen script')
    calls = []
    def request(path, data=None, _type=None, **kwargs):
        calls.append(path)
        if path == '/api/sites/site-1':
            result = {'site': {'currentVersionId': 'ver_old'}}
        elif path.startswith('/api/uploads?'):
            result = {'versionId': 'ver_staging'}
        else:
            assert path.endswith('/files/index.html') and data == b'Frozen root'
            archive.write_bytes(b'Changed during first PUT')
            result = {'ok': True}
        return 200, json.dumps(result).encode(), {}
    with snapshot_publication(archive, inputs=[]) as release:
        def guarded(*args, **kwargs):
            release.check()
            return request(*args, **kwargs)
        with pytest.raises(ValueError, match='inputs changed'):
            staged_release_upload(guarded, release.archive, title='Replay', slug='site-1',
                                  expected_version='ver_old', identity={}, key_prefix='staged-')
    assert not any('/commit' in path for path in calls)


@pytest.mark.parametrize('outcome', [
    'success', 'wrong-export', 'bad-preview', 'duplicate-export', 'wrong-share',
    'missing-field', 'empty-map',
    'linked-report', 'linked-evidence', 'linked-archive',
])
def test_browser_publication_verifies_full_export_before_binding(tmp_path, monkeypatch, outcome):
    import io

    report = tmp_path / 'report'
    report.mkdir()
    (report / 'index.html').write_text(autonomous.PUBLIC_REPORT_MARKER, encoding='utf-8')
    archive = tmp_path / 'release.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.write(report / 'index.html', 'index.html')
    profile = tmp_path / 'profile.json'
    share_url = 'https://example.invalid/v/token'
    if outcome == 'wrong-share':
        share_url = 'https://other.invalid/v/token'
    profile.write_text(json.dumps({'publication': {
        'base_url': 'https://example.invalid', 'site_slug': 'site-1',
        'expected_version': 'ver_old', 'title': 'Registry-driven report',
    }}), encoding='utf-8')
    receipt = tmp_path / 'receipt.json'
    receipt.write_text(json.dumps({'slug': 'site-1', 'share_url': share_url}), encoding='utf-8')
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    evidence_files = {}
    for name in ('manifest.json', 'run-config.json', 'attempts.jsonl', 'result-review.json'):
        (evidence / name).write_text('{}', encoding='utf-8')
        evidence_files[name] = file_sha256(evidence / name)
    if outcome == 'missing-field':
        evidence_files.pop('result-review.json')
    elif outcome == 'empty-map':
        evidence_files = {}
    proof = tmp_path / 'proof.json'
    proof.write_text(json.dumps({
        'schema': 'lex_browser_use_publication_attestation/2',
        'archive_sha256': file_sha256(archive), 'profile_sha256': file_sha256(profile),
        'report': str(report), 'report_tree_sha256': autonomous.tree_sha256(report),
        'evidence': [str(evidence)], 'evidence_files': {str(evidence): evidence_files},
    }), encoding='utf-8')
    if outcome.startswith('linked-'):
        path = {'linked-report': report, 'linked-evidence': evidence,
                'linked-archive': archive}[outcome]
        backing = path.with_name(path.name + '-original')
        path.rename(backing)
        path.symlink_to(backing, target_is_directory=backing.is_dir())
    for key, path in [('ARCHIVE', archive), ('PROFILE', profile),
                      ('RECEIPT', receipt), ('ATTESTATION', proof)]:
        monkeypatch.setattr(autonomous, key, path)
    calls = []
    uploaded = False
    def request(path, data=None, content_type=None, **kwargs):
        nonlocal uploaded
        assert kwargs['base_url'] == 'https://example.invalid'
        calls.append(path)
        if path == '/api/sites/site-1' and data is None:
            version = 'ver_new' if uploaded else 'ver_old'
            return 200, json.dumps({'site': {'currentVersionId': version}}).encode()
        if '/versions?' in path:
            uploaded = True
            assert archive.read_bytes() in data and b'Registry-driven report' in data
            return 200, b'{"versionId":"ver_new"}'
        if path == '/api/sites/site-1' and kwargs.get('method') == 'PATCH':
            assert json.loads(data)['title'] == 'Registry-driven report'
            return 200, b'{}'
        assert path == '/api/sites/site-1/export'
        if outcome in ('wrong-export', 'duplicate-export'):
            exported = io.BytesIO()
            with zipfile.ZipFile(exported, 'w') as bundle:
                bundle.writestr('index.html', 'wrong bytes')
                if outcome == 'duplicate-export':
                    with pytest.warns(UserWarning, match='Duplicate name'):
                        bundle.writestr('index.html', autonomous.PUBLIC_REPORT_MARKER)
            return 200, exported.getvalue()
        return 200, archive.read_bytes()
    monkeypatch.setattr(autonomous, 'CLIENT', SimpleNamespace(
        request=lambda base, path, *args, **kwargs: request(path, *args, base_url=base, **kwargs)))
    class Response:
        status = 200
        headers = {}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return (b'bad preview' if outcome == 'bad-preview'
                    else autonomous.PUBLIC_REPORT_MARKER.encode())
    def urlopen(request, **kwargs):
        assert request.full_url.startswith('https://example.invalid/')
        assert request.get_header('Authorization') is None
        if '/api/preview/' in request.full_url:
            assert request.get_header('X-artifact-share') == 'token'
        return Response()
    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(urllib.request, 'build_opener',
                        lambda *handlers: SimpleNamespace(open=urllib.request.urlopen))
    before = (profile.read_bytes(), proof.read_bytes(), receipt.read_bytes())
    if outcome == 'success':
        autonomous.main(create=False)
        publication = json.loads(profile.read_text(encoding='utf-8'))['publication']
        assert publication['expected_version'] == 'ver_new'
        assert publication['site_slug'] == 'site-1' and publication['share_url'] == share_url
        assert json.loads(proof.read_text(encoding='utf-8'))['profile_sha256'] == (
            file_sha256(profile))
    else:
        with pytest.raises((ValueError, RuntimeError)):
            autonomous.main(create=False)
        assert (profile.read_bytes(), proof.read_bytes(), receipt.read_bytes()) == before
        if outcome in ('missing-field', 'empty-map') or outcome.startswith('linked-'):
            assert not calls


@pytest.mark.parametrize("damage", [None, "bytes", "version", "missing", "duplicate", "cursor",
     "retry-preview", "retry-manifest", "retry-storage"])
def test_manifest_exports_verify_storage_bytes_without_per_file_service_calls(
    tmp_path, monkeypatch, damage,
):
    import io
    from urllib.parse import parse_qs, urlsplit

    from harness.reporting.publish import verify_export

    base = "https://reports.example/artifact-site"
    version = "ver_saved"
    originals = {"index.html": b"<html>Exact report</html>", "data.json": b'{"score":780}'}
    archive = tmp_path / "report.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for name, raw in originals.items():
            bundle.writestr(name, raw)
    files = [{"path": name, "size": len(raw), "url": "https://storage.example/" + name}
             for name, raw in originals.items()]
    exported = {"assembled": False, "versionId": version,
                "files": [{"path": row["path"], "size": row["size"]} for row in files]}
    if damage == "version":
        exported["versionId"] = "ver_other"
    elif damage == "missing":
        exported["files"].pop()
    elif damage == "duplicate":
        exported["files"].append(exported["files"][0])
    calls = []
    def service(path):
        calls.append(path)
        if path.endswith("/export"):
            return 200, json.dumps(exported).encode()
        assert path == "/api/sites/site-1", "Per-file service downloads are unnecessary"
        return 200, json.dumps({"site": {"currentVersionId": version}}).encode()
    root = base + "/api/delivery/d1.fixture/"
    config = {"root": root, "file": "index.html",
              "manifestUrl": root.rstrip("/") + "?__manifest=1"}
    wrapper = ('<script id="artifact-delivery-config" type="application/json">'
               + json.dumps(config) + "</script>").encode()
    monkeypatch.setattr("harness.reporting.publication.time.sleep", lambda delay: None)
    from harness.reporting import publication
    monkeypatch.setattr(publication.time, "sleep", lambda delay: None)
    attempts = {}
    def public(request, timeout):
        from http.client import IncompleteRead
        from urllib.error import HTTPError
        address = request if isinstance(request, str) else request.full_url
        attempts[address] = attempts.get(address, 0) + 1
        target = ("preview" if "/api/preview/" in address else
                  "manifest" if "__manifest" in address else "storage")
        if damage == "retry-" + target and attempts[address] == 1:
            if target == "storage":
                class Interrupted(io.BytesIO):
                    def read(self, size=-1):
                        if self.tell():
                            raise IncompleteRead(b"partial")
                        return super().read(1)
                return Interrupted(originals[urlsplit(address).path.lstrip("/")])
            raise HTTPError(address, 429 if target == "preview" else 503,
                            "temporary", {"Retry-After": "0"}, None)
        if isinstance(request, str):
            if "/api/preview/" in request:
                return io.BytesIO(wrapper)
            query = parse_qs(urlsplit(request).query)
            cursor = int(query.get("cursor", ["0"])[0])
            return io.BytesIO(json.dumps({
                "versionId": version, "files": [files[cursor]],
                "nextCursor": 1 if cursor == 0 or damage == "cursor" else None,
            }).encode())
        assert request.get_header("Authorization") is None
        assert urlsplit(request.full_url).netloc == "storage.example"
        name = urlsplit(request.full_url).path.lstrip("/")
        raw = b"corrupt" if damage == "bytes" and name == "data.json" else originals[name]
        return io.BytesIO(raw)
    monkeypatch.setattr(urllib.request, "urlopen", public)
    monkeypatch.setattr("harness.reporting.publication.time.sleep", lambda delay: None)
    if damage and not damage.startswith("retry-"):
        with pytest.raises((ValueError, RuntimeError)):
            verify_export(service, "site-1", archive, public_base=base)
    else:
        verify_export(service, "site-1", archive, public_base=base)
        assert calls.count("/api/sites/site-1") == 2
        if damage:
            assert 2 in attempts.values()


@pytest.mark.parametrize("base,share,expected", [
    ("https://reports.example", "https://reports.example/v/token", ("/v/token", "token")),
    ("https://reports.example/artifact-site",
     "https://reports.example/artifact-site/s/site-1", ("/s/site-1", "")),
    ("https://reports.example/artifact-site",
     "https://reports.example/artifact-site/v/token", ("/v/token", "token")),
])
def test_share_urls_keep_service_mount_and_main_link_access(base, share, expected):
    from harness.reporting.publication import public_share_context
    assert public_share_context(base, share) == expected
