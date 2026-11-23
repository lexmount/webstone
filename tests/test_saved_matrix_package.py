"""Saved comparisons require their original specification for packaging."""
import hashlib
import json
from dataclasses import replace

import pytest

from harness.web_fetch.publication_check import check
from harness.web_fetch.report_package import build as package
from harness.web_fetch.report_publish import publish
from lexbench_webfetch.scoring import score_attempt
from tests.test_saved_matrix_evidence import build, fixture
from tests.test_webfetch_report_publish import Service
from tests.test_webfetch_report_publish import fixture as publication_fixture


def test_saved_matrix_cannot_be_packaged_for_release(tmp_path):
    spec, _, _, _ = fixture(tmp_path)
    output = build(tmp_path, spec).parent
    with pytest.raises(ValueError, match='Diagnostic evidence cannot enter certified assembly'):
        package(output, tmp_path / 'report.zip')
    assert not (tmp_path / 'report.zip').exists()


@pytest.mark.parametrize('public_evidence', [False, True])
def test_package_saved_matrix_requires_source_bound_results(tmp_path, public_evidence):
    spec, source, record, _ = fixture(tmp_path)
    if public_evidence:
        spec['public_evidence_views'] = True
        answer = b'answer\nAuthorization: Bearer synthetic-secret'
        (source / record['artifact_path']).write_bytes(answer)
        case = json.loads((source / 'dataset.json').read_bytes())['cases'][0]
        record['metrics'] = score_attempt(case, answer, ok=True)
        raw = (json.dumps(record) + '\n').encode()
        (source / 'results.jsonl').write_bytes(raw)
        spec['sources'][0]['results']['sha256'] = hashlib.sha256(raw).hexdigest()
    output = build(tmp_path, spec).parent
    spec_path = tmp_path / 'spec.json'
    archive = tmp_path / 'report.zip'
    package(output, archive, saved_matrix_spec=spec_path)
    check(archive, output, saved_matrix_spec=spec_path)
    if public_evidence:
        answer_path = json.loads((output / 'results.jsonl').read_text())['artifact_path']
        assert b'synthetic-secret' not in (output / answer_path).read_bytes()
    else:
        target = publication_fixture(tmp_path / 'release')
        config = replace(target, archive=archive, source=output, saved_matrix_spec=spec_path)
        service = Service()
        result = publish(config, transport=service)
        assert result['slug'] == 'site-one'
        assert result['versionId'] == 'ver_new'
        assert service.uploads[0][0] == '/api/sites/site-one/versions?expected_version=ver_old'
    spec_path.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='Original saved-matrix source changed: specification'):
        check(archive, output, saved_matrix_spec=spec_path)
