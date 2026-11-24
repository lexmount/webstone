"""A frozen case selection belongs to the exact source corpus chosen for a run."""
import json

import pytest

from harness.web_fetch import cli, runner, webmainbench_dataset as dataset


class BeforeProvider(Exception):
    pass


def sample(path, parent):
    path.write_text(json.dumps({'schema_version': 1, 'parent': {'sha256': parent},
        'sampling': {'size': 1}, 'cases': [{'track_id': 'shared-case'}]}), encoding='utf-8')
    return path


@pytest.mark.parametrize('explicit', [False, True])
def test_run_rejects_original_sample_with_corrected_corpus_before_loading_env(tmp_path, monkeypatch, explicit):
    selected = sample(tmp_path / 'old.json', dataset.DATASET_SHA256)
    corrected = 'b' * 64
    monkeypatch.setattr(dataset, 'selected_input', lambda *args: (tmp_path / 'corrected.jsonl', corrected, 'c' * 40))
    calls = []
    monkeypatch.setattr(dataset, 'load_source', lambda *args: calls.append('source') or [{'track_id': 'shared-case'}])
    def environment(*args):
        calls.append('environment')
        raise BeforeProvider
    monkeypatch.setattr(runner, 'load_runtime_env', environment)
    extra = ['--dataset', str(tmp_path / 'corrected.jsonl'), '--expected-dataset-sha256', corrected] if explicit else []
    with pytest.raises(ValueError, match='sample manifest parent'):
        cli.main(['run', '--benchmark', 'webmainbench', '--providers', 'chromium',
                  '--output', str(tmp_path / 'output'), '--case-ids-file', str(selected), *extra])
    assert calls == []
    assert not (tmp_path / 'output').exists()


@pytest.mark.parametrize('parent', [dataset.DATASET_SHA256, 'b' * 64])
def test_run_accepts_selection_bound_to_actual_historical_or_corrected_corpus(tmp_path, monkeypatch, parent):
    selected = sample(tmp_path / 'selection.json', parent)
    monkeypatch.setattr(dataset, 'selected_input', lambda *args: (tmp_path / 'source.jsonl', parent, 'c' * 40))
    calls = []
    def load(path, ids, expected, **_):
        calls.append((ids, expected))
        return [{'track_id': 'shared-case'}]
    monkeypatch.setattr(dataset, 'load_source', load)
    def stop(*args):
        raise BeforeProvider
    monkeypatch.setattr(runner, 'load_runtime_env', stop)
    with pytest.raises(BeforeProvider):
        cli.main(['run', '--benchmark', 'webmainbench', '--providers', 'chromium',
                  '--output', str(tmp_path / 'output'), '--case-ids-file', str(selected)])
    assert calls == [({'shared-case'}, parent)]
    assert not (tmp_path / 'output').exists()


def test_plain_id_list_is_an_explicit_unbound_selection(tmp_path):
    path = tmp_path / 'ids.txt'
    path.write_text('shared-case\n', encoding='utf-8')
    ids, binding = dataset.load_selected_ids(path, expected_dataset_sha256='b' * 64)
    assert ids == {'shared-case'}
    assert binding['format'] == 'track_id_lines'


def test_make_historical_sample_forwards_explicit_source(tmp_path):
    import shlex
    import subprocess
    source = tmp_path / 'historical parent.jsonl'
    result = subprocess.run(['make', '-n', 'webmainbench-run', 'PROVIDER=chromium',
        'OUTPUT=' + str(tmp_path / 'output'), 'SOURCE=' + str(source)],
        check=True, capture_output=True, text=True)
    invocation = next(line for line in result.stdout.splitlines() if 'run --benchmark webmainbench' in line)
    tokens = shlex.split(invocation)
    assert tokens[tokens.index('--dataset') + 1] == str(source)
