"""The publication target every track uses, and its validation against the bound profile."""

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

VISIBILITIES = (None, "private", "unlisted", "public")


@dataclass(frozen=True)
class PublicationTarget:
    """Where a release goes on Yours and how it is shared.

    ``visibility`` ``None`` keeps the site's current access; ``share_url`` is the
    public link a track already holds, if any.
    """

    base_url: str | None = None
    site: str | None = None
    expected_version: str | None = None
    visibility: str | None = None
    share_url: str | None = None
    title: str | None = None

    def __post_init__(self):
        if self.visibility not in VISIBILITIES:
            raise ValueError("Unknown publication visibility")


def read_target(document: dict) -> PublicationTarget:
    """Read any stored target format into the one target schema.

    Stored formats are hash-bound by certificates and receipts, so each keeps its
    own keys: flat ``artifact_hub_*`` profiles, a nested ``publication`` object,
    and flat ``host``/``slug`` or ``base_url``/``slug`` target files.
    """
    if not isinstance(document, dict):
        raise ValueError("Publication target must be a JSON object")
    nested = document.get("publication")
    if "artifact_hub_base_url" not in document and isinstance(nested, dict):
        return PublicationTarget(
            base_url=nested.get("base_url"), site=nested.get("site_slug") or None,
            expected_version=nested.get("expected_version") or nested.get("version_id") or None,
            visibility=nested.get("visibility"), share_url=nested.get("share_url") or None,
            title=nested.get("title"),
        )
    if "artifact_hub_base_url" in document:
        return PublicationTarget(
            base_url=document.get("artifact_hub_base_url"),
            site=document.get("artifact_hub_site") or None,
            expected_version=document.get("artifact_hub_expected_version") or None,
            share_url=document.get("artifact_hub_share_url") or None,
            title=document.get("title"),
        )
    return PublicationTarget(
        base_url=document.get("host") or document.get("base_url"),
        site=document.get("slug") or None,
        expected_version=document.get("expected_version") or document.get("versionId") or None,
        visibility=document.get("visibility"), share_url=document.get("url") or None,
        title=document.get("title"),
    )


def publication_document(document: dict) -> dict:
    """Read flat or nested publication fields without changing their authority.

    ``artifact_hub_*`` are the stored keys of hash-bound profiles, not a current
    product name: renaming them would change the profile hashes bound by certificates.
    """
    if 'artifact_hub_base_url' in document:
        return document
    publication = document.get('publication')
    if not isinstance(publication, dict):
        return document
    return {**document,
            'artifact_hub_base_url': publication.get('base_url'),
            'artifact_hub_site': publication.get('site_slug'),
            'artifact_hub_expected_version': publication.get('version_id')}


def validate_service_base(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Publication service requires an explicit HTTPS base URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "\\" in value
        or any(ord(char) <= 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("Publication service requires an explicit HTTPS base URL")
    _ = parsed.port  # Reject malformed ports before loading a host-scoped credential.
    return value.rstrip("/")


def publication_base(
    profile_path: Path, requested_base: str | None = None, *, document: dict | None = None
) -> str:
    """Select the configured service without a deployment-specific default."""
    profile = document if document is not None else json.loads(profile_path.read_bytes())
    base = validate_service_base(profile.get("artifact_hub_base_url"))
    if requested_base is not None and validate_service_base(requested_base) != base:
        raise ValueError("Requested publication service differs from the bound profile")
    return base


def require_publication_version(
    profile_path: Path, requested_version: str | None = None, *, document: dict | None = None
) -> str:
    """Require the profile-bound current site version for a compare-and-swap update."""
    profile = document if document is not None else json.loads(Path(profile_path).read_text(encoding="utf-8"))
    expected = profile.get("artifact_hub_expected_version")
    if not isinstance(expected, str) or not expected.startswith("ver_"):
        raise ValueError("Current report profile is missing a valid artifact_hub_expected_version")
    if requested_version is not None and requested_version != expected:
        raise ValueError(
            f"Requested publication version {requested_version!r}; expected {expected!r}"
        )
    return expected


def require_publication_site(
    receipt: dict, profile_path: Path, base_url: str | None = None, *, document: dict | None = None
) -> str:
    """Refuse to publish the current report through another site's receipt."""
    profile = document if document is not None else json.loads(profile_path.read_text(encoding="utf-8"))
    expected = profile.get("artifact_hub_site")
    if not isinstance(expected, str) or not expected:
        raise ValueError("Current report profile is missing artifact_hub_site")
    if receipt.get("slug") != expected:
        raise ValueError(
            f"Publication receipt targets {receipt.get('slug')!r}; expected {expected!r}"
        )
    expected_base = validate_service_base(profile.get("artifact_hub_base_url"))
    receipt_base = (receipt.get("base_url") or "").rstrip("/")
    requested_base = (base_url or "").rstrip("/")
    if receipt_base and requested_base and receipt_base != requested_base:
        raise ValueError(
            f"Publication receipt targets {receipt_base!r}; requested {requested_base!r}"
        )
    actual_base = receipt_base or requested_base
    if actual_base != expected_base:
        raise ValueError(f"Publication receipt targets {actual_base!r}; expected {expected_base!r}")
    return expected
