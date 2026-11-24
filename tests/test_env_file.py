import os
import re
import signal
from pathlib import Path

import pytest

from harness.browser_use.runner import _load_repo_env
from harness.env_file import parse_env_file
from harness.trajectory_replay import catalog_runner
from harness.trajectory_replay.public_webarena import config as webarena_config
from harness import runtime_env
from harness.web_fetch.accounts import HOSTED_KEYS, import_credentials
from harness.web_fetch.wcxb_report_check import binary_from_env

ROOT = Path(__file__).resolve().parents[1]


def parse_text(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return parse_env_file(path)


def test_value_syntax(tmp_path):
    for line, expected in [
        ("VALUE=plain", "plain"),
        ("VALUE = spaced ", "spaced"),
        ('VALUE="double"', "double"),
        ("VALUE='single'", "single"),
        ("export VALUE=exported", "exported"),
        ("VALUE=token # note", "token"),
        ("VALUE=token#fragment", "token#fragment"),
        ('VALUE="token # kept"', "token # kept"),
        # Quoted escapes follow python-dotenv.
        (r'VALUE="line\nbreak"', "line\nbreak"),
        (r"VALUE='line\nliteral'", r"line\nliteral"),
        ("VALUE=", ""),
        # Values are data and are never expanded or executed.
        ("VALUE=$(touch marker)", "$(touch marker)"),
        ("VALUE=${HOME}", "${HOME}"),
    ]:
        assert parse_text(tmp_path, line + "\n") == {"VALUE": expected}, line


def test_mismatched_quotes_are_dropped_not_repaired_or_kept(tmp_path):
    for line in ["VALUE=\"mismatched'", "VALUE='mismatched\""]:
        assert parse_text(tmp_path, line + "\nAFTER=kept\n") == {"AFTER": "kept"}


def test_an_unterminated_quote_also_drops_a_following_quoted_assignment(tmp_path):
    # Fails closed: neither name gets a corrupted value, and the caller reports them as missing.
    path = tmp_path / ".env"
    path.write_text('BROKEN="unterminated\nNEXT_KEY="value"\nAFTER=kept\n', encoding="utf-8")
    assert parse_env_file(path) == {"AFTER": "kept"}


def test_a_value_spanning_lines_is_dropped_and_its_value_is_not_logged(tmp_path, caplog):
    # No caller has a multi-line value, and `\n` in double quotes still produces a newline.
    for text in ['BROKEN="secret\nNEXT="\nAFTER=kept\n', 'BROKEN="secret\nmore"\nAFTER=kept\n']:
        caplog.clear()
        assert parse_text(tmp_path, text) == {"AFTER": "kept"}
        assert "line 1" in caplog.text
        assert "secret" not in caplog.text


def test_last_assignment_of_a_name_wins(tmp_path):
    # Web Fetch credentials and catalog_runner already behaved this way; setdefault
    # made other Web Fetch names and all public WebArena names keep the first.
    path = tmp_path / ".env"
    path.write_text("VALUE=first\nVALUE=second\n", encoding="utf-8")
    assert parse_env_file(path) == {"VALUE": "second"}


def test_a_later_statement_without_a_value_removes_the_earlier_value(tmp_path):
    # Fails closed. Falling back would run a rotated credential on its stale value.
    for statement in ["KEY=\"new'", 'KEY="new" trailing', 'KEY="new\nvalue"', "KEY", "export KEY # note"]:
        assert parse_text(tmp_path, f"KEY=old\n{statement}\nAFTER=kept\n") == {"AFTER": "kept"}, statement
    for statement in ["KEY OTHER=nope", "KEY.dotted=\"new'", "# KEY=\"new'"]:
        assert parse_text(tmp_path, f"KEY=old\n{statement}\n") == {"KEY": "old"}, statement


def test_a_multi_line_value_removes_only_the_name_that_opens_it(tmp_path):
    # `B="` is text inside A's value, not a statement about B.
    path = tmp_path / ".env"
    path.write_text('A=old\nB=1\nA="x\nB="\nC=1\n', encoding="utf-8")
    assert parse_env_file(path) == {"B": "1", "C": "1"}


def test_lines_without_a_valid_assignment_are_ignored(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# comment\n\nNO_ASSIGNMENT\nINVALID KEY=nope\n1DIGIT=nope\ndotted.name=nope\nGOOD=yes\n",
        encoding="utf-8",
    )
    assert parse_env_file(path) == {"GOOD": "yes"}


def test_missing_file_raises_and_home_is_expanded(tmp_path, monkeypatch):
    with pytest.raises(FileNotFoundError):
        parse_env_file(tmp_path / "absent.env")
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "home.env").write_text("VALUE=home\n", encoding="utf-8")
    assert parse_env_file("~/home.env") == {"VALUE": "home"}


SHARED_SYNTAX = "A='quoted'\nexport B=exported\nC=token # note\nD=${HOME}\nE=1\nE=2\nBROKEN=\"x'\n"
SHARED_VALUES = {"A": "quoted", "B": "exported", "C": "token", "D": "${HOME}", "E": "2"}


def test_every_loader_reads_the_same_values(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(SHARED_SYNTAX, encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "process-only-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://process-only.example")
    assert catalog_runner._load_env_values(path) == SHARED_VALUES
    assert _load_repo_env(tmp_path) == {
        **SHARED_VALUES,
        **{key: os.environ[key] for key in ("COWAGENT_REPO",) if os.environ.get(key)},
    }
    for load in (runtime_env.load_env_file, webarena_config.load_runtime_env):
        for key in SHARED_VALUES:
            monkeypatch.delenv(key, raising=False)
        load(path)
        assert {key: os.environ[key] for key in SHARED_VALUES} == SHARED_VALUES


def test_wcxb_visual_check_reads_the_binary_through_the_shared_parser(tmp_path):
    path = tmp_path / ".env"
    path.write_text("export CHROMIUM_BINARY=/bin/old\nCHROMIUM_BINARY=/bin/new # note\n", encoding="utf-8")
    assert binary_from_env(path) == "/bin/new"
    for text in ["", "CHROMIUM_BINARY=\"/bin/chrome'\n"]:
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match="CHROMIUM_BINARY is missing"):
            binary_from_env(path)


def test_import_uses_the_values_the_runtime_will_read(tmp_path):
    key, other = HOSTED_KEYS[:2]
    source = tmp_path / "source.env"
    target = tmp_path / "target.env"
    for statement in ["{key}=\"mismatched'", "{key}=first\n{key}=", '{key}=stale\n{key}="new\nvalue"']:
        source.write_text(statement.format(key=key, other=other) + "\n", encoding="utf-8")
        imported, missing = import_credentials(source, target)
        assert imported == [] and key in missing, statement
        assert not target.exists()
    source.write_text(f"{key}=from-source\n", encoding="utf-8")
    for line in ["{key}=\"mismatched'", "{key}="]:
        target.write_text(line.format(key=key) + "\n", encoding="utf-8")
        assert key in import_credentials(source, target)[0]
        assert parse_env_file(target)[key] == "from-source"
    for statement, expected in [
        ('export {key} = "quoted # kept"', "quoted # kept"),
        (r'{key}="line\nbreak"', "line\nbreak"),
        ("{key}=stale\n{key}=latest # note", "latest"),
    ]:
        target.unlink()
        source.write_text(statement.format(key=key) + "\n", encoding="utf-8")
        assert key in import_credentials(source, target)[0]
        assert parse_env_file(target) == {key: expected}


def test_import_refuses_a_target_whose_broken_quote_would_absorb_the_addition(tmp_path):
    key = HOSTED_KEYS[0]
    source = tmp_path / "source.env"
    target = tmp_path / "target.env"
    source.write_text(f'{key}="from-source"\n', encoding="utf-8")
    broken = 'OTHER="unterminated\n'
    target.write_text(broken, encoding="utf-8")
    with pytest.raises(ValueError, match="unterminated quote"):
        import_credentials(source, target)
    assert target.read_text(encoding="utf-8") == broken
    assert list(tmp_path.glob(".env-import-*")) == []


def test_caller_policies_stay_outside_the_parser(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("WEBARENA_REDDIT_USERNAME=from-file\nEXA_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("WEBARENA_REDDIT_USERNAME", "exported")
    monkeypatch.setenv("EXA_API_KEY", "exported")
    webarena_config.load_runtime_env(path)
    assert os.environ["WEBARENA_REDDIT_USERNAME"] == "exported"
    assert os.environ["EXA_API_KEY"] == "exported"
    runtime_env.load_env_file(path, override_credentials=True)
    assert os.environ["WEBARENA_REDDIT_USERNAME"] == "exported"
    assert os.environ["EXA_API_KEY"] == "from-file"
    with pytest.raises(ValueError, match="repository .env"):
        runtime_env.load_env_file(path, credentials_allowed=False)
    symlink = tmp_path / "link.env"
    symlink.symlink_to(path)
    with pytest.raises(ValueError, match="regular file"):
        catalog_runner._load_env_values(symlink)


ENV_PARSER = re.compile(
    r"^\s*(?:from|import) dotenv\b"
    r"|[\"']=[\"'] (?:not )?in \w*line"
    r"|\w*line\w*(?:\.strip\(\))?\.r?(?:split|partition)\(\s*[\"']=[\"']"
    r"|\.startswith\(\s*f?[\"'][^\"']*=[\"']"
    r"|\(\?:export\\s",
    re.M,
)


def test_only_the_shared_module_parses_env_files():
    allowed = {"src/harness/env_file.py"}
    sources = [*(ROOT / "src/harness").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]
    found = [
        str(path.relative_to(ROOT))
        for path in sources
        if str(path.relative_to(ROOT)) not in allowed and ENV_PARSER.search(path.read_text("utf-8"))
    ]
    assert found == []


def test_env_parser_guard_catches_hand_written_forms_but_not_argument_parsers():
    for source in [
        "import dotenv",
        "if '=' in raw_line:",
        "line.split('=', 1)[1].strip().strip('\"')",
        'if line.startswith(f"{key}="):',
        r'ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?(\w+)=(.*)$")',
    ]:
        assert ENV_PARSER.search(source), source
    for source in ['name, separator, path = value.partition("=")',
                   '{v.split("=", 1)[0]: Path(v.split("=", 1)[1]) for v in args.source}']:
        assert not ENV_PARSER.search(source), source


def test_local_setting_uses_one_precedence(tmp_path, monkeypatch) -> None:
    from harness.env_file import local_setting, select_env_file

    local = tmp_path / "local.env"
    local.write_text("DEMO_SETTING=from-file\nDEMO_SPACED=' keep spaces '\n", encoding="utf-8")
    monkeypatch.delenv("DEMO_SETTING", raising=False)
    monkeypatch.delenv("DEMO_SPACED", raising=False)

    assert local_setting("DEMO_SETTING", local) == "from-file"
    assert local_setting("DEMO_SPACED", local) == " keep spaces "
    assert local_setting("DEMO_SETTING", tmp_path / "absent.env") == ""

    monkeypatch.setenv("DEMO_SETTING", "exported")
    assert local_setting("DEMO_SETTING", local) == "exported"
    monkeypatch.delenv("DEMO_SETTING")

    select_env_file(local)
    try:
        assert local_setting("DEMO_SETTING") == "from-file"
    finally:
        select_env_file(None)


def test_local_setting_stops_on_an_unusable_file(tmp_path, monkeypatch) -> None:
    from harness.env_file import local_setting

    monkeypatch.delenv("DEMO_SETTING", raising=False)
    target = tmp_path / "real.env"
    target.write_text("DEMO_SETTING=value\n", encoding="utf-8")
    link = tmp_path / "link.env"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="regular file"):
        local_setting("DEMO_SETTING", link)
    with pytest.raises(ValueError, match="regular file"):
        local_setting("DEMO_SETTING", tmp_path)


def _fail_instead_of_hanging(signum, frame):
    raise AssertionError("reading the env file blocked")


def test_every_env_file_reader_rejects_a_non_regular_file(tmp_path, monkeypatch) -> None:
    from harness.env_file import local_setting

    path = tmp_path / "settings.env"
    os.mkfifo(path)
    monkeypatch.delenv("DEMO_SETTING", raising=False)
    previous = signal.signal(signal.SIGALRM, _fail_instead_of_hanging)
    signal.alarm(10)
    try:
        for read in (
            parse_env_file,
            lambda value: local_setting("DEMO_SETTING", value),
            runtime_env.load_env_file,
            catalog_runner._load_env_values,
        ):
            with pytest.raises(ValueError, match="regular file"):
                read(path)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def test_a_missing_env_file_keeps_its_error_and_a_link_to_a_file_still_parses(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_env_file(tmp_path / "absent.env")
    target = tmp_path / "real.env"
    target.write_text("DEMO_SETTING=value\n", encoding="utf-8")
    link = tmp_path / "link.env"
    link.symlink_to(target)
    assert parse_env_file(link) == {"DEMO_SETTING": "value"}


def test_the_null_device_reads_as_an_empty_env_file(monkeypatch) -> None:
    assert parse_env_file(os.devnull) == {}
    monkeypatch.setattr(os, "environ", dict(os.environ))
    runtime_env.load_runtime_env(Path(os.devnull))
