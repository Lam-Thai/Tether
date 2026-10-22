"""`tether-eval`'s guard rails: everything it refuses before touching a database or Gemini."""

from pathlib import Path

import pytest
from pydantic import SecretStr

from core.config import settings
from evals import cli
from evals.cases import load_suite
from evals.cli import CRASHED, REFUSED, main
from evals.scorers import SCORERS

REAL_RUN = cli._run  # kept before the tripwire replaces it


def load_suite_core():
    return load_suite("core", known_scorers=frozenset(SCORERS))


@pytest.fixture(autouse=True)
def no_real_run(monkeypatch):
    """A guard test must never reach the database or Gemini."""

    def tripwire(*args, **kwargs):
        raise AssertionError("tether-eval started a real run")

    monkeypatch.setattr(cli, "_run", tripwire)


def test_check_validates_the_suite_without_calling_anything(capsys):
    main(["--suite", "core", "--check"])
    assert "cases OK" in capsys.readouterr().out


def test_tag_filter_narrows_the_suite(capsys):
    main(["--check", "--tag", "unanswerable"])
    assert "suite core: 8 cases OK" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["--suite", "no-such-suite"],
        ["--tag", "no-such-tag"],
        ["--rpm", "0"],
        ["--max-requests", "0"],
        ["--concurrency", "0"],
    ],
)
def test_bad_arguments_are_refused(argv, monkeypatch):
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("test-key"))
    with pytest.raises(SystemExit) as exited:
        main(argv)
    assert exited.value.code == REFUSED


def test_refuses_to_run_against_production(monkeypatch, capsys):
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(SystemExit) as exited:
        main(["--suite", "core"])
    assert exited.value.code == REFUSED
    assert "ENVIRONMENT=production" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["--suite", "core"], ["--purge"]])
def test_refuses_a_remote_database_unless_allowed(argv, monkeypatch, capsys):
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "database_url", "postgresql://u:p@db.example.com/neondb")
    with pytest.raises(SystemExit) as exited:
        main(argv)
    assert exited.value.code == REFUSED
    assert "--allow-remote-db" in capsys.readouterr().err


def test_a_crashed_run_has_its_own_exit_code(monkeypatch, capsys):
    from evals.runner import TIER_LIMITS

    class Boom:
        def __init__(self, *args, **kwargs) -> None: ...

        async def run(self, suite):
            raise ConnectionRefusedError("postgresql://secret@host")

    monkeypatch.setattr(cli, "EvalRunner", Boom)
    monkeypatch.setattr("core.llm.get_llm_client", lambda: None)
    code = REAL_RUN(load_suite_core(), TIER_LIMITS["free"], "free", Path("unused"))
    assert code == CRASHED
    err = capsys.readouterr().err
    assert "ConnectionRefusedError" in err and "secret" not in err


def test_refuses_to_run_without_a_gemini_key(monkeypatch, capsys):
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "gemini_api_key", None)
    with pytest.raises(SystemExit) as exited:
        main(["--suite", "core"])
    assert exited.value.code == REFUSED
    assert "GEMINI_API_KEY" in capsys.readouterr().err
