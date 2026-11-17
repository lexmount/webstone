"""Dataset releases preserve committed inputs and reject broken populations."""
import hashlib
import json
import subprocess
import tarfile

import pytest

from harness.benchmarks.publication import DatasetRelease


def test_dataset_version_survives_a_hub_rebuild(tmp_path):
    from dataclasses import replace

    root, revision, _ = checkout(tmp_path, 'websearch')
    release = replace(DatasetRelease.from_git(root, 'websearch', revision), dataset_version='1.2.0')
    output = tmp_path / 'versioned'
    assert release.build(output)['edition']['version'] == '1.2.0'
    assert 'v1.2.0' in (output / 'README.md').read_text(encoding='utf-8')
    git(output, 'init', '-q')
    git(output, 'add', '.')
    git(output, 'commit', '-qm', 'Versioned fixture')
    (output / 'VERSIONING.md').write_text('Committed release policy.\n', encoding='utf-8')
    git(output, 'add', 'VERSIONING.md')
    git(output, 'commit', '-qm', 'Update policy without rewriting frozen inputs')
    (output / 'VERSIONING.md').write_text('Uncommitted edit.\n', encoding='utf-8')
    rebuilt = DatasetRelease.from_git(output, 'websearch', git(output, 'rev-parse', 'HEAD'))
    assert rebuilt.build(tmp_path / 'rebuilt')['edition']['version'] == '1.2.0'
    assert (tmp_path / 'rebuilt/VERSIONING.md').read_text(encoding='utf-8') == 'Committed release policy.\n'


def test_version_is_visible_on_a_card_without_subheadings(tmp_path):
    from dataclasses import replace

    root, revision, _ = checkout(tmp_path, 'websearch')
    release = replace(
        DatasetRelease.from_git(root, 'websearch', revision), dataset_version='1.0.0',
        card_body='# Custom benchmark\n\nOriginal description.')
    release.build(tmp_path / 'release')
    assert 'v1.0.0' in (tmp_path / 'release/README.md').read_text(encoding='utf-8')


@pytest.mark.parametrize('version', [None, '1.0.0'])
def test_new_proposals_require_an_unused_explicit_version(tmp_path, monkeypatch, version):
    import sys
    from types import SimpleNamespace

    from harness.benchmarks import publication

    root, revision, _ = checkout(tmp_path, 'websearch')

    class Api:
        def list_repo_refs(self, *args, **kwargs):
            return SimpleNamespace(tags=[SimpleNamespace(name='v1.0.0')])

    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(HfApi=Api))
    args = ['publish', '--checkout', str(root), '--track', 'websearch', '--revision', revision,
            '--output', str(tmp_path / 'release'), '--create-pr', 'owner/data']
    if version is not None:
        args += ['--dataset-version', version]
    monkeypatch.setattr(sys, 'argv', args)
    with pytest.raises(SystemExit if version is None else ValueError):
        publication.main()


@pytest.mark.parametrize('version', ['v1.0.0', '1.0', '01.2.3', 'main', ''])
def test_dataset_release_rejects_invalid_version_labels(tmp_path, version):
    from dataclasses import replace

    root, revision, _ = checkout(tmp_path, 'websearch')
    release = replace(DatasetRelease.from_git(root, 'websearch', revision), dataset_version=version)
    with pytest.raises(ValueError, match='dataset version'):
        release.build(tmp_path / 'invalid')


def git(checkout, *args):
    return subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.org", *args],
                          cwd=checkout, check=True, capture_output=True, text=True).stdout.strip()


def checkout(tmp_path, track, defect=None):
    root = tmp_path / "source"
    root.mkdir()
    prefix = root / f"src/lexbench_{track}/definitions"
    prefix.mkdir(parents=True)
    cases = [{"id": "first", "query": "Which source?", "url": "https://example.org", "requirements": ["a", "b"]}]
    if defect == "duplicate":
        cases.append(cases[0])
    raw = json.dumps({"cases": cases}).encode()
    path = "tasks.json"
    (prefix / path).write_bytes(raw)
    binding = {"path": path, "count": 2 if defect == "count" else len(cases),
               "sha256": "0" * 64 if defect == "hash" else hashlib.sha256(raw).hexdigest()}
    registry = {"datasets": {"sample": binding}}
    if track == "webfetch":
        registry = {"suites": [{"id": "sample", "directory": ".", "case_count": binding["count"],
                                "files": {"tasks.json": binding["sha256"]}}]}
        # Keep the directory spelling identical to real suite registries.
        (prefix / "evaluation").mkdir()
        (prefix / "evaluation/tasks.json").write_bytes(raw)
        registry["suites"][0]["directory"] = "evaluation"
    (prefix / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    for name in ["LICENSE", "NOTICE", "THIRD_PARTY.md", "VERSIONING.md"]:
        (root / name).write_text("Retain this notice.\n", encoding="utf-8")
    if defect == "symlink":
        (prefix / "linked.json").symlink_to("tasks.json")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "Fixture")
    return root, git(root, "rev-parse", "HEAD"), cases


@pytest.mark.parametrize("track", ["webfetch", "websearch"])
def test_release_preserves_tasks_not_working_tree_or_private_files(tmp_path, track):
    root, revision, cases = checkout(tmp_path, track)
    (root / ".env").write_text("PRIVATE=not-publishable", encoding="utf-8")
    (root / "VERSIONING.md").write_text("Uncommitted version policy", encoding="utf-8")
    (root / f"src/lexbench_{track}/definitions/tasks.json").write_text("modified after commit", encoding="utf-8")
    release = DatasetRelease.from_git(root, track, revision)
    left, right = tmp_path / "left", tmp_path / "right"
    manifest = release.build(left)
    release.build(right)
    assert (left / "definitions.tar.gz").read_bytes() == (right / "definitions.tar.gz").read_bytes()
    rows = [json.loads(line) for line in (left / "data/sample.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [json.loads(row["task"]) for row in rows] == cases
    assert manifest["source_revision"] == revision
    assert (left / "VERSIONING.md").read_text(encoding="utf-8") == "Retain this notice.\n"
    assert not (left / ".env").exists()
    with tarfile.open(left / "definitions.tar.gz") as archive:
        for item in archive:
            assert archive.extractfile(item).read() == (left / item.name).read_bytes()
    for name, binding in manifest["files"].items():
        assert hashlib.sha256((left / name).read_bytes()).hexdigest() == binding["sha256"]


@pytest.mark.parametrize("track", ["webfetch", "websearch"])
@pytest.mark.parametrize("defect", ["hash", "count", "duplicate", "symlink"])
def test_invalid_release_is_rejected(tmp_path, track, defect):
    root, revision, _ = checkout(tmp_path, track, defect)
    with pytest.raises(ValueError):
        DatasetRelease.from_git(root, track, revision).build(tmp_path / "release")


def test_release_requires_commit_and_empty_destination(tmp_path):
    root, revision, _ = checkout(tmp_path, "websearch")
    with pytest.raises(ValueError, match="full Git commit"):
        DatasetRelease.from_git(root, "websearch", "main")
    release = DatasetRelease.from_git(root, "websearch", revision)
    release.build(tmp_path / "release")
    with pytest.raises(ValueError, match="empty"):
        release.build(tmp_path / "release")


def test_jsonl_derivative_retains_upstream_identifiers_and_source(tmp_path):
    root, revision, _ = checkout(tmp_path, "websearch")
    release = DatasetRelease.from_git(root, "websearch", revision)
    records = [{"track_id": "upstream-1", "url": "https://example.org", "html": "<main>Content</main>",
                "groundtruth_content": "Content"}]
    raw = b"".join((json.dumps(row) + "\n").encode() for row in records)
    files = dict(release.files)
    files["definitions/records.jsonl"] = raw
    files["definitions/registry.json"] = json.dumps({"datasets": {"corrected": {
        "path": "records.jsonl", "format": "jsonl", "id_field": "track_id", "count": 1,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }}}).encode()
    derived = DatasetRelease("webmainbench", revision, files, "https://github.com/lexmount/WebMainBench")
    result = derived.build(tmp_path / "release")
    exported = json.loads((tmp_path / "release/data/corrected.jsonl").read_text(encoding="utf-8"))
    assert exported["id"] == "upstream-1"
    assert json.loads(exported["task"]) == records[0]
    assert result["source_repository"] == "https://github.com/lexmount/WebMainBench"


@pytest.mark.parametrize("missing", ["yaml", "card"])
@pytest.mark.parametrize("existing_empty", [False, True])
def test_build_prerequisites_fail_before_output_and_allow_retry(
    tmp_path, monkeypatch, missing, existing_empty
):
    import builtins
    from pathlib import Path

    from harness.benchmarks import publication

    root, revision, _ = checkout(tmp_path, "websearch")
    release = DatasetRelease.from_git(root, "websearch", revision)
    output = tmp_path / "release"
    if existing_empty:
        output.mkdir()
    original_import = builtins.__import__
    original_read = Path.read_text

    def blocked_import(name, *args, **kwargs):
        if name == "yaml":
            raise ModuleNotFoundError("publishing extra is unavailable")
        return original_import(name, *args, **kwargs)

    def blocked_card(path, *args, **kwargs):
        if path == publication.CARDS / "websearch.md":
            raise OSError("card is unreadable")
        return original_read(path, *args, **kwargs)

    with monkeypatch.context() as failure:
        if missing == "yaml":
            failure.setattr(builtins, "__import__", blocked_import)
        else:
            failure.setattr(Path, "read_text", blocked_card)
        with pytest.raises((ModuleNotFoundError, OSError)):
            release.build(output)
    assert not output.exists() or list(output.iterdir()) == []
    result = release.build(output)
    assert (output / "README.md").is_file()
    assert result["archive"]["path"] == "definitions.tar.gz"


@pytest.mark.parametrize("inline_card", [False, True])
def test_release_utf8_roundtrip_under_ascii_locale(tmp_path, inline_card):
    import os
    import sys

    from harness.benchmarks import publication

    cards = tmp_path / "cards"
    cards.mkdir()
    (cards / "metadata.json").write_bytes((publication.CARDS / "metadata.json").read_bytes())
    (cards / "websearch.md").write_text("# 评测 café\n\n{{dataset_summary}}\n", encoding="utf-8")
    script = r'''
import hashlib
import json
import locale
import sys
from pathlib import Path
from harness.benchmarks import publication
assert sys.flags.utf8_mode == 0
assert locale.getencoding().lower() not in {'utf-8', 'utf8'}
publication.CARDS = Path(sys.argv[1])
raw = json.dumps({'cases': [{'id': 'one', 'query': '\u68c0\u7d22 caf\u00e9'}]}, ensure_ascii=False).encode('utf-8')
registry = {'datasets': {'sample': {'path': 'tasks.json', 'count': 1,
                                  'sha256': hashlib.sha256(raw).hexdigest()}}}
body = '# \u8bc4\u6d4b caf\u00e9\n' if sys.argv[3] == 'True' else None
release = publication.DatasetRelease('websearch', 'a' * 40, {
    'definitions/registry.json': json.dumps(registry).encode('utf-8'),
    'definitions/tasks.json': raw,
}, card_body=body, language_annotations={})
output = Path(sys.argv[2])
release.build(output)
assert '\u8bc4\u6d4b caf\u00e9' in (output / 'README.md').read_text(encoding='utf-8')
assert (output / 'definitions/tasks.json').read_bytes() == raw
viewer = json.loads((output / 'data/sample.jsonl').read_bytes())
assert json.loads(viewer['task'])['query'] == '\u68c0\u7d22 caf\u00e9'
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(cards), str(tmp_path / "release"), str(inline_card)],
        env={**os.environ, "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0", "LC_ALL": "C"},
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr


def published_hub(tmp_path, track="websearch"):
    """Build a source release and commit it as a Hugging Face hub checkout."""
    source, revision, _ = checkout(tmp_path, track)
    root = tmp_path / "hub"
    DatasetRelease.from_git(source, track, revision).build(root)
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "Hub")
    return root


def commit_manifest(root, **changes):
    manifest = json.loads((root / "manifest.json").read_bytes())
    (root / "manifest.json").write_text(json.dumps({**manifest, **changes}), encoding="utf-8")
    git(root, "commit", "-qam", "Manifest change")
    return git(root, "rev-parse", "HEAD")


@pytest.mark.parametrize("claimed", ["websearch", "trajectory-replay"])
def test_hub_track_identity_comes_from_committed_manifest(tmp_path, claimed):
    root = published_hub(tmp_path)
    revision = git(root, "rev-parse", "HEAD")
    # A dirty working-tree claim must neither repair nor break the committed identity.
    (root / "manifest.json").write_text(json.dumps({"track": "trajectory-replay"}), encoding="utf-8")
    output = tmp_path / "release"
    if claimed == "websearch":
        release = DatasetRelease.from_git(root, claimed, revision)
        assert release.build(output)["track"] == "websearch"
    else:
        with pytest.raises(ValueError, match="track"):
            DatasetRelease.from_git(root, claimed, revision).build(output)
        assert not output.exists()


def test_hub_track_cannot_be_supplied_by_uncommitted_manifest(tmp_path):
    root = published_hub(tmp_path)
    git(root, "rm", "-q", "--cached", "manifest.json")
    git(root, "commit", "-qm", "Hub without identity")
    with pytest.raises(ValueError, match="manifest"):
        DatasetRelease.from_git(root, "websearch", git(root, "rev-parse", "HEAD"))


@pytest.mark.parametrize('filters', [
    [], {'unknown': []}, {'viewer_exclude_datasets': 'sample'},
    {'viewer_exclude_datasets': [True]}, {'viewer_exclude_tracks': []},
    {'viewer_exclude_tracks': {'sample': 'dynamic'}},
    {'viewer_exclude_tracks': {'sample': [False]}},
    {'viewer_exclude_tracks': {'': ['dynamic']}},
])
def test_invalid_committed_population_filters_fail_before_build(tmp_path, filters):
    root = published_hub(tmp_path)
    revision = commit_manifest(root, viewer_filters=filters)
    output = tmp_path / 'release'
    with pytest.raises(ValueError, match='viewer filters'):
        DatasetRelease.from_git(root, 'websearch', revision).build(output)
    assert not output.exists()


def test_hub_definitions_come_from_the_manifest_bound_archive(tmp_path, monkeypatch):
    root = published_hub(tmp_path)
    expected = DatasetRelease.from_git(root, 'websearch', git(root, 'rev-parse', 'HEAD')).files
    # Large hubs keep definitions only in the archive (LexBench-NewsXLM, LexBench-LiveWeb-IE).
    git(root, 'rm', '-rq', '--cached', 'definitions')
    git(root, 'commit', '-qm', 'Archive-only hub')
    (root / 'definitions').mkdir(exist_ok=True)
    git(root, 'add', '-f', '--', str(next((root / 'definitions').rglob('*.json'))))
    git(root, 'commit', '-qm', 'One mirrored file')
    assert DatasetRelease.from_git(root, 'websearch', git(root, 'rev-parse', 'HEAD')).files == expected
    # Archive bytes and every member stay bound by the committed manifest.
    manifest = json.loads((root / 'manifest.json').read_bytes())
    name = next(name for name in manifest['files'] if name.startswith('definitions/'))
    tampered = {**manifest['files'], name: {**manifest['files'][name], 'sha256': '0' * 64}}
    with pytest.raises(ValueError, match='does not match the release manifest: ' + name):
        DatasetRelease.from_git(root, 'websearch', commit_manifest(root, files=tampered))
    # A member whose declared size disagrees is rejected before its payload is read.
    smaller = {**manifest['files'], name: {**manifest['files'][name], 'size': 1}}
    revision = commit_manifest(root, files=smaller)
    extract = tarfile.TarFile.extractfile
    def unread(archive, member):
        assert member.name != name, 'member payload read before size check'
        return extract(archive, member)
    monkeypatch.setattr(tarfile.TarFile, 'extractfile', unread)
    with pytest.raises(ValueError, match='does not match the release manifest: ' + name):
        DatasetRelease.from_git(root, 'websearch', revision)
    monkeypatch.undo()
    with pytest.raises(ValueError, match='archive does not match'):
        DatasetRelease.from_git(root, 'websearch', commit_manifest(
            root, files=manifest['files'], archive={**manifest['archive'], 'size': 1}))
    missing = {**manifest['files'], 'definitions/absent.json': {'sha256': '0' * 64, 'size': 1}}
    with pytest.raises(ValueError, match="manifest's definition files"):
        DatasetRelease.from_git(root, 'websearch', commit_manifest(
            root, files=missing, archive=manifest['archive']))


def test_runtime_settings_survive_original_source_publication_and_hub_rebuild(tmp_path, monkeypatch):
    from harness.benchmarks import publication
    root, _, _ = checkout(tmp_path, 'webmainbench')
    registry = root / 'src/lexbench_webmainbench/definitions/registry.json'
    values = json.loads(registry.read_bytes())
    scorer = {'repository': 'https://github.com/lexmount/WebMainBench.git', 'revision': 'b' * 40}
    values.update(default_dataset='sample', scorer=scorer)
    registry.write_text(json.dumps(values), encoding="utf-8")
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'Bind runtime choices')
    revision = git(root, 'rev-parse', 'HEAD')
    output = tmp_path / 'published'
    monkeypatch.setattr('sys.argv', ['publish', '--checkout', str(root), '--track', 'webmainbench',
                                    '--revision', revision, '--output', str(output)])
    publication.main()
    manifest = json.loads((output / 'manifest.json').read_bytes())
    assert manifest['scorer'] == scorer
    assert manifest['default_dataset'] == 'sample'
    git(output, 'init', '-q')
    git(output, 'add', '.')
    git(output, 'commit', '-qm', 'Published edition')
    published_revision = git(output, 'rev-parse', 'HEAD')
    dirty = json.loads((output / 'manifest.json').read_bytes())
    dirty['scorer']['revision'] = 'c' * 40
    dirty['viewer_filters'] = {'viewer_exclude_datasets': ['sample']}
    (output / 'manifest.json').write_text(json.dumps(dirty), encoding="utf-8")
    (output / 'VERSIONING.md').write_text('Uncommitted policy', encoding="utf-8")
    rebuilt = DatasetRelease.from_git(output, 'webmainbench', published_revision).build(tmp_path / 'rebuilt')
    assert rebuilt['scorer'] == scorer
    assert rebuilt['viewer_filters'] == manifest['viewer_filters']
    assert (tmp_path / 'rebuilt/VERSIONING.md').read_text(encoding="utf-8") == 'Retain this notice.\n'
    assert rebuilt['default_dataset'] == 'sample'


def test_hub_rebuild_keeps_committed_edition_tools_and_snapshot_bindings(tmp_path):
    root, revision, _ = checkout(tmp_path, 'websearch')
    plain = DatasetRelease.from_git(root, 'websearch', revision).build(tmp_path / 'published')
    output = tmp_path / 'published'
    # A hub built without edition metadata or tools gains neither on rebuild.
    git(output, 'init', '-q')
    git(output, 'add', '.')
    git(output, 'commit', '-qm', 'Hub without edition')
    rebuilt = DatasetRelease.from_git(output, 'websearch', git(output, 'rev-parse', 'HEAD')).build(tmp_path / 'bare')
    assert 'edition' not in plain and 'edition' not in rebuilt
    assert not any((tmp_path / 'bare' / name).exists() for name in ('tools', 'Makefile', 'snapshots.json'))
    assert ({p.relative_to(tmp_path / 'bare') for p in (tmp_path / 'bare').rglob('*')}
            == {p.relative_to(output) for p in output.rglob('*') if '.git' not in p.parts})

    edition = {'tag': 'v3.0.0', 'version': '3.0.0', 'task_count': 1, 'population_sha256': 'd' * 64}
    manifest = json.loads((output / 'manifest.json').read_bytes())
    (output / 'manifest.json').write_text(json.dumps({**manifest, 'edition': edition}), encoding='utf-8')
    tools = {'tools/rebuild_publication.py': b'raise SystemExit(0)\n',
             'tools/test_population.py': b'# kept\r\n', 'Makefile': b'check:\n\ttrue\n',
             # Web Extract hubs gain URL bindings from harness.web_extract.snapshots.publish.
             'snapshots.json': b'{"fixture_base": "https://snapshots.example/v1"}\n'}
    for name, raw in tools.items():
        (output / name).parent.mkdir(exist_ok=True)
        (output / name).write_bytes(raw)
    git(output, 'add', '.')
    git(output, 'commit', '-qm', 'Hub edition with tools')
    hub_revision = git(output, 'rev-parse', 'HEAD')
    (output / 'tools/rebuild_publication.py').write_bytes(b'uncommitted\n')
    result = DatasetRelease.from_git(output, 'websearch', hub_revision).build(tmp_path / 'rebuilt')
    rebuilt = tmp_path / 'rebuilt'
    assert result['edition'] == edition
    assert 'version' not in result
    assert DatasetRelease.from_git(output, 'websearch', hub_revision).dataset_version == '3.0.0'
    assert json.loads((rebuilt / 'manifest.json').read_bytes())['edition'] == edition
    assert {name: (rebuilt / name).read_bytes() for name in tools} == tools
    assert not set(tools) & set(result['files'])
    with tarfile.open(rebuilt / 'definitions.tar.gz') as archive:
        assert not set(tools) & set(archive.getnames())


@pytest.mark.parametrize(('legacy', 'edition', 'conflict'), [
    ('3.0.0', {'version': '3.0.0', 'tag': 'v3.0.0'}, False),
    ('3.1.0', {'version': '3.0.0', 'tag': 'v3.0.0'}, True),
    ('3.0.0', {'tag': 'v3.0.0'}, False),
    ('3.0.0', {'tag': 'v2.0.0'}, True),
])
def test_hub_release_numbers_have_one_authority(tmp_path, legacy, edition, conflict):
    root, revision, _ = checkout(tmp_path, 'websearch')
    output = tmp_path / 'hub'
    manifest = DatasetRelease.from_git(root, 'websearch', revision).build(output)
    manifest.update(version=legacy, edition=edition)
    (output / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    git(output, 'init', '-q')
    git(output, 'add', '.')
    git(output, 'commit', '-qm', 'Historical version fields')
    if conflict:
        with pytest.raises(ValueError, match='version.*disagree'):
            DatasetRelease.from_git(output, 'websearch', git(output, 'rev-parse', 'HEAD'))
    else:
        result = DatasetRelease.from_git(output, 'websearch', git(output, 'rev-parse', 'HEAD')).build(tmp_path / 'rebuilt')
        assert result['edition'] == {'version': '3.0.0', 'tag': 'v3.0.0'}
        assert 'version' not in result


def test_committed_paths_cannot_leave_the_release(tmp_path):
    root = published_hub(tmp_path)
    # Git stores a crafted "tools/../../escaped" tree that ls-tree prints verbatim.
    def tree(entries):
        return subprocess.run(['git', '-C', str(root), 'mktree'], check=True, text=True,
                              capture_output=True, input=''.join(entries)).stdout.strip()
    blob = subprocess.run(['git', '-C', str(root), 'hash-object', '-w', '--stdin'], check=True,
                          text=True, capture_output=True, input='escaped\n').stdout.strip()
    inner = tree([f'100644 blob {blob}\tescaped\n'])
    middle = tree([f'040000 tree {inner}\t..\n'])
    tools = tree([f'040000 tree {middle}\t..\n'])
    base = git(root, 'rev-parse', 'HEAD^{tree}')
    entries = subprocess.run(['git', '-C', str(root), 'ls-tree', base], check=True, text=True,
                             capture_output=True).stdout
    top = tree([entries, f'040000 tree {tools}\ttools\n'])
    revision = git(root, 'commit-tree', top, '-m', 'crafted', '-p', 'HEAD')
    output = tmp_path / 'nested/out'
    with pytest.raises(ValueError, match='leaves the release'):
        DatasetRelease.from_git(root, 'websearch', revision).build(output)
    assert not (tmp_path / 'escaped').exists() and not (tmp_path / 'nested/escaped').exists()


@pytest.mark.parametrize('settings', [
    {'default_dataset': 'missing'},
    {'scorer': {'repository': 'https://github.com/example/scorer', 'revision': 'main'}},
])
def test_invalid_runtime_choices_fail_before_output(tmp_path, settings):
    from dataclasses import replace
    root, revision, _ = checkout(tmp_path, 'websearch')
    release = replace(DatasetRelease.from_git(root, 'websearch', revision), **settings)
    output = tmp_path / 'release'
    with pytest.raises(ValueError, match='Default dataset|Scorer requires'):
        release.build(output)
    assert not output.exists()


@pytest.mark.parametrize('source_version', [None, '0.9.0'])
def test_publish_opens_a_review_pr_without_updating_main_or_creating_tags(
        tmp_path, monkeypatch, capsys, source_version):
    import sys
    from types import SimpleNamespace
    from harness.benchmarks import publication

    root, revision, _ = checkout(tmp_path, 'websearch')
    if source_version:
        hub = tmp_path / 'hub'
        manifest = DatasetRelease.from_git(root, 'websearch', revision).build(hub)
        manifest['edition'] = {'version': source_version, 'tag': 'v' + source_version}
        (hub / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        git(hub, 'init', '-q')
        git(hub, 'add', '.')
        git(hub, 'commit', '-qm', 'Previous released edition')
        root, revision = hub, git(hub, 'rev-parse', 'HEAD')
    output = tmp_path / 'proposal'
    calls = []

    class ReviewApi:
        def list_repo_refs(self, repo_id, *, repo_type):
            return SimpleNamespace(tags=[])

        def get_repo_discussions(self, repo_id, *, repo_type):
            return []

        def repo_info(self, *, repo_id, repo_type):
            assert (repo_id, repo_type) == ('Lexmount/Test-Dataset', 'dataset')
            return SimpleNamespace(sha='a' * 40, siblings=[
                SimpleNamespace(rfilename='.gitattributes'),
                SimpleNamespace(rfilename='data/retired.jsonl')])

        def upload_folder(self, **kwargs):
            assert kwargs['create_pr'] is True
            assert kwargs['parent_commit'] == 'a' * 40
            assert kwargs['delete_patterns'] == ['data/retired.jsonl']
            assert (kwargs['folder_path'] / 'manifest.json').is_file()
            manifest = json.loads((kwargs['folder_path'] / 'manifest.json').read_bytes())
            assert manifest['edition'] == {'version': '1.0.0', 'tag': 'v1.0.0'}
            assert 'version' not in manifest
            calls.append(kwargs)
            return SimpleNamespace(oid='b' * 40,
                pr_url='https://huggingface.co/datasets/Lexmount/Test-Dataset/discussions/7')
        # No main-branch commit, merge, or tag operation is available here.

    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(HfApi=ReviewApi))
    monkeypatch.setattr(sys, 'argv', ['publication', '--checkout', str(root), '--track', 'websearch',
        '--revision', revision, '--output', str(output), '--dataset-version', '1.0.0',
        '--create-pr', 'Lexmount/Test-Dataset'])
    publication.main()
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert len(calls) == 1
    assert result['state'] == 'review_pending'
    assert result['pr_revision'] == 'b' * 40
    assert 'revision' not in result
    assert result['pull_request'].endswith('/discussions/7')


@pytest.mark.parametrize(('tags', 'current', 'pending', 'candidate', 'error'), [
    (['v3.0.0'], '3.0.0', [], '2.9.0', 'released version'),
    (['v3.0.0'], '3.1.0', [], '3.1.0', 'main manifest'),
    (['v3.0.0'], '3.1.0', ['3.2.0'], '3.2.0', 'open pull request'),
    (['v3.0.0'], '3.1.0', ['3.2.0'], '3.3.0', None),
    ([], None, [], '1.0.0', None),
])
def test_proposals_respect_released_main_and_pending_versions(
        tmp_path, monkeypatch, tags, current, pending, candidate, error):
    import sys
    from types import SimpleNamespace
    from harness.benchmarks.publication import proposal_target

    manifests = {}
    for revision, version in [('a' * 40, current), *[
            (f'refs/pr/{i}', v) for i, v in enumerate(pending, 1)]]:
        if version is not None:
            path = tmp_path / (revision.replace('/', '-') + '.json')
            path.write_text(json.dumps({'edition': {'version': version, 'tag': 'v' + version}}), encoding='utf-8')
            manifests[revision] = path

    def download(repo_id, filename, *, repo_type, revision, token, endpoint):
        assert (repo_id, filename, repo_type) == ('Lexmount/Test', 'manifest.json', 'dataset')
        return manifests[revision]

    class Api:
        token = 'test'
        endpoint = 'https://hub.example'

        def list_repo_refs(self, repo_id, *, repo_type):
            return SimpleNamespace(tags=[SimpleNamespace(name=t) for t in tags])

        def repo_info(self, **kwargs):
            return SimpleNamespace(sha='a' * 40, siblings=[
                SimpleNamespace(rfilename='manifest.json')] if current else [])

        def get_repo_discussions(self, repo_id, *, repo_type):
            return [SimpleNamespace(is_pull_request=True, status='open', num=i)
                    for i in range(1, len(pending) + 1)]

    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(hf_hub_download=download))
    monkeypatch.setitem(sys.modules, 'huggingface_hub.errors', SimpleNamespace(EntryNotFoundError=FileNotFoundError))
    if error:
        with pytest.raises(ValueError, match=error):
            proposal_target(Api(), 'Lexmount/Test', candidate)
    else:
        assert proposal_target(Api(), 'Lexmount/Test', candidate).sha == 'a' * 40


@pytest.mark.parametrize("reference", ["os:getcwd", "harness.web_fetch.models", ""])
def test_derived_viewer_fields_only_resolve_named_harness_functions(reference):
    from harness.benchmarks.publication import _derived_field

    with pytest.raises(ValueError, match="Invalid derived viewer field"):
        _derived_field(reference)


def test_webfetch_viewer_configurations_follow_card_subset_order(tmp_path):
    root = tmp_path / "source"
    prefix = root / "src/lexbench_webfetch/definitions"
    (prefix / "webfetch").mkdir(parents=True)
    # Register the later subset first so only the card order can restore it.
    cases = [{"id": "doc", "track": "document", "url": "https://example.org/a.pdf"},
             {"id": "page", "track": "web", "url": "https://example.org"}]
    raw = json.dumps({"cases": cases}).encode()
    (prefix / "webfetch/tasks.json").write_bytes(raw)
    (prefix / "registry.json").write_text(json.dumps({"suites": [{
        "id": "webfetch", "directory": "webfetch", "case_count": len(cases),
        "files": {"tasks.json": hashlib.sha256(raw).hexdigest()}}]}), encoding="utf-8")
    for name in ["LICENSE", "NOTICE", "THIRD_PARTY.md"]:
        (root / name).write_text("Retain this notice.\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "Fixture")

    manifest = DatasetRelease.from_git(root, "webfetch", git(root, "rev-parse", "HEAD")).build(tmp_path / "out")

    assert [row["config"] for row in manifest["viewer"]["configurations"]] == ["general-web", "embedded-documents"]
    rows = [json.loads(line) for line in (tmp_path / "out/data/general-web.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["benchmark_category"] for row in rows] == ["web"]


def test_component_license_is_preserved_from_its_committed_bytes(tmp_path):
    root, _, _ = checkout(tmp_path, "websearch")
    component = b"Apache-2.0 applies only to the authored annotation supplement.\n"
    (root / "LICENSE-annotations").write_bytes(component)
    git(root, "add", "LICENSE-annotations")
    git(root, "commit", "-qm", "Component-specific license")
    revision = git(root, "rev-parse", "HEAD")
    (root / "LICENSE-annotations").write_bytes(b"Uncommitted replacement")
    release = DatasetRelease.from_git(root, "websearch", revision)
    assert release.files["LICENSE-annotations"] == component


def test_repair_proposal_rejects_main_advance_before_upload(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from harness.benchmarks import publication

    release = SimpleNamespace(dataset_version='2.0.0', build=lambda output: {})
    monkeypatch.setattr(publication, 'proposal_target', lambda *args: SimpleNamespace(sha='b'*40))
    # No write capability: the changed parent must be rejected before upload.
    with pytest.raises(ValueError, match='main changed'):
        publication.propose_release(release, tmp_path, 'Lexmount/Test',
                                    api=object(), parent_revision='a'*40)


@pytest.mark.parametrize('defect', [None, 'unapplied-answer', 'missing-evidence'])
def test_simpleqa_release_requires_applied_and_source_bound_repairs(tmp_path, defect):
    from copy import deepcopy
    from lexbench_websearch.simpleqa import extend_reference_urls

    before = dict(query='Who wrote the book?', reference_answer='Wrong Author',
                  reference_urls=['https://publisher.test/page'])
    after = dict(before, reference_answer='Correct Author')
    recipe = dict(schema='simpleqa-case-repairs/1', predecessor=dict(revision='a'*40, sha256='b'*64),
                  root_cause='Wrong original author', regression_ids=['corrected-factual-reference'], repairs=[dict(
                      case_id='q', before=before, after=after, root_cause='Wrong original author', evidence=[dict(
                          url=after['reference_urls'][0], sha256='c'*64, quote='Correct Author wrote the book.')])])
    dataset = extend_reference_urls(dict(cases=[dict(id='q', **after)], case_repairs=deepcopy(recipe)), [])
    if defect == 'unapplied-answer':
        dataset['cases'][0]['reference_answer'] = before['reference_answer']
    elif defect == 'missing-evidence':
        dataset['case_repairs']['repairs'][0]['evidence'] = []
    raw = json.dumps(dataset).encode()
    registry = dict(datasets={'factual-v1': dict(path='datasets/factual-v1.json', count=1,
                                               sha256=hashlib.sha256(raw).hexdigest())})
    release = DatasetRelease('simpleqa-verified', 'a'*40, {
        'definitions/registry.json': json.dumps(registry).encode(),
        'definitions/datasets/factual-v1.json': raw,
    }, card_body='# Synthetic corrected release\n', language_annotations={})
    if defect:
        with pytest.raises(ValueError):
            release.build(tmp_path / 'release')
    else:
        assert release.build(tmp_path / 'release')['datasets']['factual-v1']['count'] == 1


@pytest.mark.parametrize('missing_inventory', [False, True])
def test_simpleqa_release_replays_full_reference_audit(tmp_path, missing_inventory):
    from lexbench_websearch.simpleqa import extend_reference_urls

    dataset = extend_reference_urls(dict(cases=[dict(id='q', query='Question', reference_answer='Answer',
        reference_urls=['https://publisher.test/page'])]), [])
    if missing_inventory:
        dataset.pop('reference_equivalence_observations')
    raw = json.dumps(dataset).encode()
    registry = dict(datasets={'factual-v1': dict(path='datasets/factual-v1.json', count=1,
                                               sha256=hashlib.sha256(raw).hexdigest())})
    release = DatasetRelease('simpleqa-verified', 'a' * 40, {
        'definitions/registry.json': json.dumps(registry).encode(),
        'definitions/datasets/factual-v1.json': raw,
    }, card_body='# Synthetic reference release\n', language_annotations={})
    if missing_inventory:
        with pytest.raises(ValueError, match='full reference capture inventory'):
            release.build(tmp_path / 'release')
    else:
        assert release.build(tmp_path / 'release')['datasets']['factual-v1']['count'] == 1
