"""Lossless case-sharded delivery for hosts with a bounded file count.

The report renderer and checks remain authoritative. This exporter transports
their generated pages and raw evidence without generating alternative findings.
"""

import base64
import gzip
import json
from pathlib import Path
from urllib.parse import quote, urlsplit

from bs4 import BeautifulSoup

from harness.record_io import bytes_sha256
from harness.web_search.core import write_json
from harness.web_search.messages import zh_text

ASSETS = Path(__file__).with_name("assets")
BRIDGE = "\n" + ASSETS.joinpath("delivery-bridge.js").read_text(encoding="utf-8").replace(
    "__CHINESE_LABEL__", zh_text("Chinese"))
LOADER = "\n" + ASSETS.joinpath("delivery-loader.js").read_text(encoding="utf-8").replace(
    "__DOWNLOAD_LABEL_ZH__", zh_text("Download original response"))


def _delivery_link(value, packed=()):
    if not value or value.startswith(("http://", "https://", "#")):
        return value
    if "/details/" in value or value.startswith("details/"):
        return "detail.html#" + quote(value, safe="")
    if value.startswith("calls/"):
        return "evidence.html#" + quote(value, safe="")
    if value in packed:
        return "evidence.html#" + quote(value, safe="")
    return value


def transform_page(text, *, packed=(), comparison_files=None, counterpart_url=None):
    doc = BeautifulSoup(text, "html.parser")
    bridge = doc.new_tag("script")
    bridge.string = BRIDGE
    doc.head.append(bridge)
    # Static links are rewritten at export, including diagnosis links assigned later.
    for node in doc.select("[href],[data-detail]"):
        for attr in ("href", "data-detail"):
            value = node.get(attr, "")
            if value:
                if attr == 'href' and node.find_parent('nav') and counterpart_url and node.get_text(strip=True) in ('English', zh_text('Chinese')):
                    node[attr] = counterpart_url
                    continue
                node[attr] = _delivery_link(value, packed)
    # Paginated comparison cards are created after load, so their canonical
    # links live in structured data rather than DOM attributes.
    for node in doc.select("script[data-comparison-data]"):
        data = json.loads(node.string)
        for case in data["cases"]:
            for result in case["outputs"].values():
                if result is not None:
                    result["detail"] = _delivery_link(result["detail"])
        node.string = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
        if comparison_files is not None and len(node.string.encode()) >= 1024 * 1024:
            raw = node.string.encode('utf-8')
            path = 'comparison-data/' + bytes_sha256(raw) + '.json.gz'
            comparison_files[path] = gzip.compress(raw, mtime=0)
            node['data-comparison-src'] = path
            node.clear()
    return str(doc)


def _public_files(manifest, language=None):
    routes = set(manifest['routes']) | set(manifest.get('language_notices', []))
    if language is not None:
        routes = {path for path in routes if path.startswith('en/') == (language == 'en')}
    return routes | {a['path'] for a in manifest['bundle']} | {'manifest.json'}


def _english_entry(text):
    page = BeautifulSoup(text, 'html.parser')
    for base in page.select('base'):
        base.decompose()
    return str(page)


def export(source, destination, *, language=None, counterpart_url=None):
    """Keep one shard per case, including both languages and all physical calls."""
    source, destination = Path(source), Path(destination)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if language is not None:
        target = urlsplit(counterpart_url or '')
        if language not in manifest.get('languages', []) or target.scheme != 'https' or not target.hostname or target.username or target.password:
            raise ValueError('A language edition requires a generated language and public HTTPS counterpart')
    elif counterpart_url is not None:
        raise ValueError('Select a delivery language for a counterpart link')
    destination.mkdir(parents=True, exist_ok=False)
    public_files = _public_files(manifest, language)
    shards, mapping, hashes = {}, {}, {}
    details = {}
    for route in manifest['routes']:
        if route not in public_files:
            continue
        if route.startswith('details/') or '/details/' in route:
            details.setdefault(Path(route).stem, []).append(route)
    for row in manifest["rows"]:
        shard = shards.setdefault(row["case_id"], {})
        paths = [row["evidence"]["path"], *[a["evidence"]["path"] for a in row.get("attempts", [])]]
        paths += details.get(row['id'], [])
        for path in paths:
            raw = (source / path).read_bytes()
            # JSON transport must not normalize CRLF or decode arbitrary provider bytes.
            shard[path] = {"base64": base64.b64encode(raw).decode("ascii")}
            mapping[path] = "case-data/" + row["case_id"] + ".json.gz"
            hashes[path] = bytes_sha256(raw)
    for cid, data in shards.items():
        target = destination / "case-data" / (cid + ".json.gz")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False).encode(), mtime=0))
    comparison_files = {}
    for path in sorted(public_files - set(mapping)):
        raw = (source / path).read_bytes()
        if path == 'manifest.json' or not path.endswith('.json') or len(raw) < 1024 * 1024:
            continue
        packed_json = json.dumps({path: {
            'base64': base64.b64encode(raw).decode('ascii')
        }}).encode()
        shard = 'evidence-data/' + bytes_sha256(packed_json) + '.json.gz'
        target = destination / shard
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(packed_json, mtime=0))
        mapping[path], hashes[path] = shard, bytes_sha256(raw)
    for path in sorted(public_files - set(mapping)):
        raw = (source / path).read_bytes()
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(transform_page(raw.decode(), packed=mapping, comparison_files=comparison_files,
                                         counterpart_url=counterpart_url).encode() if path.endswith(".html") else raw)
    for path, raw in comparison_files.items():
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    write_json(destination / "delivery-index.json", {
        "schema": "search-report-delivery/2",
        "source_manifest_sha256": bytes_sha256((source / "manifest.json").read_bytes()),
        "files": mapping, "sha256": hashes,
        **({'language': language, 'counterpart_url': counterpart_url} if language else {}),
    })
    entry = 'en/index.html' if language == 'en' else 'index.html'
    shell = BeautifulSoup((destination / entry).read_text(encoding="utf-8"), "html.parser")
    if language == 'en':
        for base in shell.select('base'):
            base.decompose()
        (destination / 'index.html').write_text(
            _english_entry((destination / entry).read_text(encoding='utf-8')), encoding='utf-8')
    shell.main.clear()
    pre = shell.new_tag("pre")
    pre["data-delivery-target"] = ""
    pre.string = "Loading saved evidence…" if language == 'en' else "Loading saved evidence… / " + zh_text("Loading saved evidence…")
    shell.main.append(pre)
    # Drop the report's listeners until the requested page has been installed.
    for script in shell.select("body script, [data-report-language-entry]"):
        script.decompose()
    loader = shell.new_tag("script")
    loader.string = LOADER
    shell.body.append(loader)
    for route in ("detail.html", "evidence.html"):
        (destination / route).write_text(str(shell), encoding="utf-8")
    verify(source, destination)
    return destination


def verify(source, destination):
    """Every virtual file restores exactly; every ordinary page has one transform."""
    source, destination = Path(source), Path(destination)
    index = json.loads((destination / "delivery-index.json").read_text(encoding="utf-8"))
    language, counterpart_url = index.get('language'), index.get('counterpart_url')
    if bytes_sha256((source / "manifest.json").read_bytes()) != index["source_manifest_sha256"]:
        raise ValueError("Delivery source manifest changed")
    cache = {}
    for path, shard in index["files"].items():
        if shard not in cache:
            cache[shard] = json.loads(gzip.decompress((destination / shard).read_bytes()))
        value = cache[shard][path]
        raw = base64.b64decode(value["base64"], validate=True)
        if bytes_sha256(raw) != index["sha256"][path] or raw != (source / path).read_bytes():
            raise ValueError("Packed evidence changed")
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    comparison_files = {}
    for path in _public_files(manifest, language):
        if path in index["files"]:
            continue
        raw = (source / path).read_bytes()
        expected = transform_page(raw.decode(), packed=index['files'], comparison_files=comparison_files,
                                  counterpart_url=counterpart_url).encode() if path.endswith(".html") else raw
        if (destination / path).read_bytes() != expected:
            raise ValueError("Delivery page changed")
    for path, raw in comparison_files.items():
        target = destination / path
        if target.is_symlink() or not target.is_file() or target.read_bytes() != raw:
            raise ValueError('Comparison data changed')
    if language == 'en' and (destination / 'index.html').read_text(encoding='utf-8') != _english_entry(
            (destination / 'en/index.html').read_text(encoding='utf-8')):
        raise ValueError('English delivery entry changed')
    return True
