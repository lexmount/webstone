"""Generated WCXB selections retain the source edition consumed by runners."""
import json
import sys

import pytest

from harness.benchmarks import data
from harness.web_fetch import runner, wcxb_dataset
from harness.web_fetch.benchmarks import BENCHMARKS
from tests.test_wcxb_selected_edition import ReachedCollection, corpus, preflight


@pytest.mark.parametrize('scope,expected,source_option', [
    ('qualified', 504, False), ('full', 504, True), ('subset', 200, False)])
def test_generate_then_consume_qualified_edition(tmp_path, monkeypatch, scope, expected, source_option):
    source, edition_path, edition = corpus(tmp_path, 504)
    if source_option:
        (source.parent / 'tasks.json').write_text(edition_path.read_text(encoding='utf-8'), encoding='utf-8')
    monkeypatch.setattr(data, 'definition_path', lambda track, relative: source if relative == 'source' else edition_path)
    output = tmp_path / 'selection.json'
    arguments = ['wcxb-select', '--output', str(output)]
    if source_option:
        arguments += ['--source', str(source)]
    if scope != 'subset':
        arguments.append('--qualified-test' if scope == 'qualified' else '--full-test')
    monkeypatch.setattr(sys, 'argv', arguments)
    wcxb_dataset.main()
    selected_manifest = json.loads(output.read_text(encoding='utf-8'))
    assert selected_manifest['schema_version'] == 2
    assert selected_manifest['source_count'] == 504
    expected_id = {'qualified': edition['dataset_id'], 'full': 'wcxb-v1-test-full',
                   'subset': 'wcxb-v1-test-stratified-200'}[scope]
    assert selected_manifest['dataset_id'] == expected_id
    if scope != 'qualified':
        assert selected_manifest['parent_dataset_id'] == edition['dataset_id']
    assert 'source_commit' not in selected_manifest
    assert selected_manifest['source_sha256'] == edition['source_sha256']
    assert selected_manifest['size'] == len(selected_manifest['cases']) == expected
    original_cases = {row['file_id']: row for row in edition['cases']}
    assert selected_manifest['cases'] == [original_cases[row['file_id']] for row in selected_manifest['cases']]
    records, digest = wcxb_dataset.load_wcxb_source(source, manifest=output)
    selected, _ = wcxb_dataset.validate_manifest(records, digest, selected_manifest)
    assert len(records) == 504 and len(selected) == expected
    args, called = preflight(tmp_path, monkeypatch, source, output)
    with pytest.raises(ReachedCollection):
        runner.run_frozen_cohort(BENCHMARKS.get(args.benchmark), args)
    assert called == [True]
    assert not args.output.exists()


@pytest.mark.parametrize('split,count,versioned', [('test', 511, False), ('train', 1497, True)])
def test_discovered_and_explicit_historical_manifests_select_identical_population(tmp_path, split, count, versioned):
    source, selected, manifest = corpus(tmp_path, count, split=split, historical=True)
    if not versioned:
        manifest.pop('schema_version')
        selected.write_text(json.dumps(manifest), encoding='utf-8')
    (source.parent / 'tasks.json').write_text(selected.read_text(encoding='utf-8'), encoding='utf-8')
    implicit_records, implicit_hash = wcxb_dataset.load_wcxb_source(source, split)
    explicit_records, explicit_hash = wcxb_dataset.load_wcxb_source(source, split, manifest=selected)
    assert len(implicit_records) == len(explicit_records) == count
    assert implicit_records == explicit_records and implicit_hash == explicit_hash
    (source / split / 'ground-truth/0000.json').unlink()
    for selection in ({}, {'manifest': selected}):
        with pytest.raises(ValueError, match=f'Expected {count}'):
            wcxb_dataset.load_wcxb_source(source, split, **selection)


def test_historical_generator_keeps_original_schema_and_qualified_selection(tmp_path, monkeypatch):
    source, _, _ = corpus(tmp_path, 511, historical=True)
    output = tmp_path / 'historical.json'
    monkeypatch.setattr(sys, 'argv', ['wcxb-select', '--source', str(source),
                                     '--qualified-test', '--output', str(output)])
    wcxb_dataset.main()
    manifest = json.loads(output.read_text(encoding='utf-8'))
    assert manifest['schema_version'] == 1 and manifest['size'] == 504
    records, digest = wcxb_dataset.load_wcxb_source(source, manifest=output)
    selected, _ = wcxb_dataset.validate_manifest(records, digest, manifest)
    assert len(records) == 511 and len(selected) == 504


@pytest.mark.parametrize('changed', ['source_count', 'scope', 'reference'])
def test_generated_subset_does_not_weaken_edition_or_selection_checks(tmp_path, monkeypatch, changed):
    source, edition_path, _ = corpus(tmp_path, 504)
    (source.parent / 'tasks.json').write_text(edition_path.read_text(encoding='utf-8'), encoding='utf-8')
    output = tmp_path / 'subset.json'
    monkeypatch.setattr(sys, 'argv', ['wcxb-select', '--source', str(source), '--output', str(output)])
    wcxb_dataset.main()
    manifest = json.loads(output.read_text(encoding='utf-8'))
    if changed == 'source_count':
        manifest['source_count'] = 200
    elif changed == 'scope':
        manifest['scope'] = 'qualified_test'
    else:
        manifest['cases'][0]['reference'] = 'Foreign reference'
    output.write_text(json.dumps(manifest), encoding='utf-8')
    args, called = preflight(tmp_path, monkeypatch, source, output)
    with pytest.raises(ValueError):
        runner.run_frozen_cohort(BENCHMARKS.get(args.benchmark), args)
    assert called == []


def test_generator_rejects_stale_task_fields_in_selected_qualified_edition(tmp_path, monkeypatch):
    source, edition_path, edition = corpus(tmp_path, 504)
    edition['cases'][0]['reference'] = 'Foreign reference'
    edition_path.write_text(json.dumps(edition), encoding='utf-8')
    monkeypatch.setattr(data, 'definition_path', lambda track, relative: source if relative == 'source' else edition_path)
    output = tmp_path / 'selection.json'
    monkeypatch.setattr(sys, 'argv', ['wcxb-select', '--qualified-test', '--output', str(output)])
    with pytest.raises(ValueError, match='task fields differ'):
        wcxb_dataset.main()
    assert not output.exists()
