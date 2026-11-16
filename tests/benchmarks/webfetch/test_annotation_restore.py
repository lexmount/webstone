"""Restoration must preserve task acceptance and reject unbound reference bytes."""
import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from harness.record_io import canonical_json_sha256
from lexbench_webfetch.benchmark import public_task, restore_annotations
from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.content_scopes import REQUIREMENT_DEFAULTS
from tests.benchmarks.webfetch.test_content_scopes import annotated_case


def inputs(tmp_path):
    annotated = annotated_case()
    base = deepcopy(annotated)
    del base['content_scopes']
    base['source_benchmark'] = {'input_media_kind': 'html_page'}
    base['reference_answer'] = 'Complete article body\nContact publisher'
    annotated.update(deepcopy(base))
    for profile in annotated['content_scopes'].values():
        if isinstance(profile, dict):
            profile['requirements'] = {**deepcopy(REQUIREMENT_DEFAULTS), **profile['requirements']}
            profile['evidence']['source_sha256'] = canonical_json_sha256(base)
            profile['requirements']['source_benchmark'] = deepcopy(base['source_benchmark'])
    root = tmp_path/'definitions'
    root.mkdir()
    public = {'id': 'annotations', 'name': 'Annotated tasks', 'cases': [public_task(annotated)]}
    (root/'tasks.json').write_text(json.dumps(public), encoding='utf-8')
    (root/'source-inputs.json').write_text(json.dumps({'dataset_sha256': 'a'*64}), encoding='utf-8')
    source = tmp_path/'source.json'
    source.write_text(json.dumps({'cases': [base]}), encoding='utf-8')
    return root, source


def test_restored_annotations_keep_both_scopes_and_label_derived_inputs(tmp_path):
    root, source = inputs(tmp_path)
    output = tmp_path/'restored'
    with patch('lexbench_webfetch.benchmark.suite', return_value=({'kind': 'annotation_cohort'}, root)):
        path = restore_annotations('annotations', source, output)
    case = json.loads(path.read_text(encoding='utf-8'))['cases'][0]
    assert evaluate_response(case, b'Complete article body', content_scope='main')['passed']
    assert not evaluate_response(case, b'Complete article body', content_scope='full')['passed']
    assert evaluate_response(case, b'Complete article body\nContact publisher', content_scope='full')['passed']
    proof = json.loads((output/'input-provenance.json').read_text(encoding='utf-8'))
    assert proof['verified_source_tasks'] == proof['verified_public_contracts'] == 1
    assert proof['private_export_bytes_reproduced'] is False


@pytest.mark.parametrize('corruption', ['source', 'missing_reference', 'population'])
def test_restoration_rejects_changed_source_or_missing_bound_text(tmp_path, corruption):
    root, source = inputs(tmp_path)
    if corruption in {'source', 'population'}:
        data = json.loads(source.read_text(encoding='utf-8'))
        if corruption == 'source':
            data['cases'][0]['reference_answer'] = 'Invented replacement'
        else:
            data['cases'] = []
        source.write_text(json.dumps(data), encoding='utf-8')
    else:
        data = json.loads((root/'tasks.json').read_text(encoding='utf-8'))
        data['cases'][0]['content_scopes']['full']['requirements']['body_requirements'] = {
            'regions': [{'text_binding': {'sha256': 'f'*64, 'chars': 3}}]}
        (root/'tasks.json').write_text(json.dumps(data), encoding='utf-8')
    output = tmp_path/'restored'
    with patch('lexbench_webfetch.benchmark.suite', return_value=({'kind': 'annotation_cohort'}, root)):
        with pytest.raises(ValueError):
            restore_annotations('annotations', source, output)
    assert not output.exists()
