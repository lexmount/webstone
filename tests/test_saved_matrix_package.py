"""Publish bound diagnostic compositions without certifying unbound matrices."""
import hashlib
from pathlib import Path

import pytest

from harness.web_fetch.report_package import build as package
from tests.test_saved_matrix_evidence import build, fixture


def test_saved_matrix_cannot_be_packaged_for_release(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    output = build(tmp_path, spec).parent
    with pytest.raises(ValueError, match='Diagnostic evidence cannot enter certified assembly or reports'):
        package(output, tmp_path / 'report.zip')
    assert not (tmp_path / 'report.zip').exists()


def test_composition_package_keeps_nested_evidence_and_frozen_scores(tmp_path):
    import json
    import zipfile
    from urllib.parse import quote
    from harness.reporting.packs import DecompressedPackCache, read_packed_manifest, reconstruct_packed_file
    from harness.web_fetch.publication_check import check
    from harness.web_fetch.report_saved_matrix import build_saved_matrix
    from tests.test_saved_matrix_composition import reports

    spec, entries = reports(tmp_path / 'sources')
    for entry in entries:
        root = Path(entry['root'])
        page = root/'lazy-evidence.html'
        page.write_text('<html><body><pre data-file="references/first--same.txt"></pre></body></html>')
        audit_path = root/'saved-matrix-audit.json'
        audit = json.loads(audit_path.read_bytes())
        audit['files'][page.name] = hashlib.sha256(page.read_bytes()).hexdigest()
        viewer = root/'evidence/viewer/original.html'
        viewer.parent.mkdir(parents=True, exist_ok=True)
        links = '<a href="../references/first--same.txt">Source</a>'
        for name in ('a#b.txt', 'a?b.txt', 'a%b.txt'):
            artifact = root/'references'/name
            artifact.write_text('Encoded filename evidence')
            audit['files']['references/'+name] = hashlib.sha256(artifact.read_bytes()).hexdigest()
            links += '<a href="../references/'+quote(name, safe='')+'?download=1#raw">Source</a>'
        viewer.write_text('<html><body>'+links+'</body></html>')
        audit['files'][viewer.relative_to(root).as_posix()] = hashlib.sha256(viewer.read_bytes()).hexdigest()
        audit_path.write_text(json.dumps(audit))
        entry['audit']['sha256'] = hashlib.sha256(audit_path.read_bytes()).hexdigest()
    value = json.loads(spec.read_bytes())
    value['saved_reports'] = entries
    value['frozen_sources'] = True
    spec.write_text(json.dumps(value))
    source = build_saved_matrix(spec, tmp_path / 'report').parent
    prior = (source / 'results.jsonl').read_bytes()
    archive = tmp_path / 'report.zip'
    package(source, archive)
    check(archive, source)
    assert (source / 'results.jsonl').read_bytes() == prior
    with zipfile.ZipFile(archive) as bundle, DecompressedPackCache(bundle.read) as packs:
        manifest = read_packed_manifest(bundle.read)
        for entry in entries:
            prefix = 'evidence/saved-reports/' + entry['id'] + '/'
            page = reconstruct_packed_file(manifest, prefix + 'lazy-evidence.html', packs)
            assert ('data-file="' + prefix).encode() in page
            viewer = reconstruct_packed_file(manifest, prefix + 'evidence/viewer/original.html', packs)
            assert b'href="../../references/first--same.txt"' in viewer
            for name in ('a#b.txt', 'a?b.txt', 'a%b.txt'):
                assert ('href="../../references/'+quote(name, safe='')+'?download=1#raw"').encode() in viewer
        assert reconstruct_packed_file(manifest, 'results.jsonl', packs) == prior
    results = source/'results.jsonl'
    altered = [json.loads(line) for line in prior.splitlines()]
    altered[0]['elapsed_ms'] = 0
    results.write_text(''.join(json.dumps(row)+'\n' for row in altered))
    audit_path = source/'saved-matrix-audit.json'
    audit_bytes = audit_path.read_bytes()
    audit = json.loads(audit_bytes)
    audit['files']['results.jsonl'] = hashlib.sha256(results.read_bytes()).hexdigest()
    audit_path.write_text(json.dumps(audit))
    with pytest.raises(ValueError, match='projection differs'):
        package(source, tmp_path/'altered-result.zip')
    results.write_bytes(prior)
    audit_path.write_bytes(audit_bytes)
    artifact = source / json.loads(prior.splitlines()[0])['artifact_path']
    artifact.write_text('changed')
    with pytest.raises(ValueError, match='changed|differs'):
        package(source, tmp_path / 'tampered.zip')
