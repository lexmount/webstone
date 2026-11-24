"""Native methods accept declared upgrades and reject mismatched installations."""
from harness.distribution import source_path

import shutil
import subprocess
from pathlib import Path

import pytest

from harness.web_fetch.providers.cowagent import CowAgentBuiltinProvider
from harness.web_fetch.providers.subprocess_jsonl import SubprocessJsonlProvider

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def template(tmp_path_factory):
    """One committed upstream checkout and version stubs, built once per worker.

    The stubs live outside the copied checkout so each is created once: macOS
    assesses every newly created executable on its first launch (about 1.5 s).
    """
    root = tmp_path_factory.mktemp('native-runtime')
    tools = root / 'tools'
    tools.mkdir()
    for name, version in [('node', 'v30.1.2'), ('pnpm', '15.2.3')]:
        path = tools / name
        path.write_text('#!/bin/sh\nprintf "' + version + '\\n"\n', encoding='utf-8')
        path.chmod(0o755)
    repo = root / 'upstream'
    for name in ['agent/tools/web_fetch/web_fetch.py', 'agent/tools/utils/url_safety.py',
                 'src/agents/tools/web-fetch.ts', 'packages/web/web-fetch-http/src/provider.ts',
                 'pnpm-lock.yaml']:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('synthetic source\n', encoding='utf-8')
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture',
                    '-c', 'user.email=fixture@example.test', 'commit', '-qm', 'fixture'],
                   check=True)
    revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'],
                                       text=True).strip()
    return repo, tools, revision


@pytest.fixture
def checkout(template, tmp_path):
    repo, tools, revision = template
    copy = tmp_path / 'upstream'
    shutil.copytree(repo, copy, symlinks=True)
    return copy, tools, revision


# Every method runs 'none', which also proves a wrong revision fails revalidation.
# Repository checks are shared, so each other change runs once.
@pytest.mark.parametrize('method,change', [
    ('cowagent_builtin', 'none'), ('openclaw_builtin', 'none'),
    ('deepseek_harness_builtin', 'none'), ('cowagent_builtin', 'dirty'),
    ('openclaw_builtin', 'missing-source'), ('cowagent_builtin', 'wrong-python'),
    ('openclaw_builtin', 'wrong-node'), ('openclaw_builtin', 'wrong-manager'),
    ('deepseek_harness_builtin', 'stale-worker')])
def test_declared_native_installation_is_authoritative(checkout, method, change):
    repo, tools, revision = checkout
    if method == 'cowagent_builtin':
        required = ['agent/tools/web_fetch/web_fetch.py', 'agent/tools/utils/url_safety.py']
    elif method == 'openclaw_builtin':
        required = ['src/agents/tools/web-fetch.ts', 'pnpm-lock.yaml']
    else:
        required = ['packages/web/web-fetch-http/src/provider.ts', 'pnpm-lock.yaml',
                    'node_modules/.pnpm/.browser-harness-dsh-worker.mts']
        deployed = repo / required[-1]
        deployed.parent.mkdir(parents=True)
        shutil.copyfile(source_path('harness/web_fetch/workers/dsh_webfetch_worker.mts', root=ROOT), deployed)
    config = {'repo_path': str(repo), 'expected_repo_revision': revision,
              'required_paths': ['{repo_path}/' + name for name in required]}
    if method != 'cowagent_builtin':
        worker = ('openclaw_webfetch_worker.mts' if method == 'openclaw_builtin'
                  else 'dsh_webfetch_worker.mts')
        config.update(method_type=method, command=[str(tools / 'node')],
                      expected_node_version='v30.1.2')
        config['required_paths'].append('{harness_root}/harness/web_fetch/workers/' + worker)
    if method == 'deepseek_harness_builtin':
        config['required_paths'].append('{harness_root}/harness/web_fetch/workers/dsh_output.mjs')
    if method == 'openclaw_builtin':
        config.update(package_manager_command=str(tools / 'pnpm'),
                      expected_package_manager_version='15.2.3')
    provider = (CowAgentBuiltinProvider if method == 'cowagent_builtin'
                else SubprocessJsonlProvider)('custom-name', config)
    config = provider.config
    if change == 'dirty':
        (repo / required[0]).write_text('dirty\n', encoding='utf-8')
    elif change == 'missing-source':
        (repo / required[0]).unlink()
    elif change == 'wrong-node':
        config['expected_node_version'] = 'v0.0.0'
    elif change == 'wrong-manager':
        config['expected_package_manager_version'] = '0.0.0'
    elif change == 'wrong-python':
        config['expected_python_version'] = 'Python 0.0.0'
    elif change == 'stale-worker':
        deployed.write_text('old worker\n', encoding='utf-8')
    errors = provider.validate()
    if change == 'none':
        assert errors == []
        identity = provider._implementation_identity
        assert identity['repository']['head'] == revision
        assert str(repo) not in str(identity)
        config['expected_repo_revision'] = '0' * 40
        assert provider.validate()
        assert not hasattr(provider, '_implementation_identity')
    else:
        assert errors
        if change == 'stale-worker':
            assert any(error.startswith('runtime files differ:') for error in errors)
        assert not hasattr(provider, '_implementation_identity')


@pytest.mark.parametrize('paths', [None, 'source.py', [None]])
def test_invalid_required_path_configuration_fails_preflight(checkout, paths):
    repo, tools, revision = checkout
    provider = SubprocessJsonlProvider('custom-name', {
        'repo_path': str(repo), 'command': [str(tools / 'node')],
        'expected_repo_revision': revision, 'required_paths': paths,
    })
    assert provider.validate()
    assert not hasattr(provider, '_implementation_identity')
