"""Package-surface tests: exports and CLI parsing. The real tests live in
test_vfs.py (v0 read path) and test_publish.py (v1 publisher).
"""

import pytest

import swarmlite
from swarmlite.cli import main


def test_exports():
    assert callable(swarmlite.connect)
    assert callable(swarmlite.publish)
    assert swarmlite.__version__


def test_cli_help_parses():
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_cli_requires_command():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code != 0


def test_cli_rejects_copy_pasted_placeholders(capsys):
    assert main(["query", "bzz://<root>/demo.db", "SELECT 1"]) == 1
    err = capsys.readouterr().err
    assert "placeholder <root>" in err and "swarmlite publish" in err

    assert main(["publish", "site.db", "--stamp", "<batchID>"]) == 1
    assert "placeholder <batchID>" in capsys.readouterr().err


def test_cli_errors_are_one_line_not_tracebacks(capsys, monkeypatch):
    monkeypatch.delenv("SWARMLITE_DEBUG", raising=False)
    # bad swarm reference reaches swarmfs, which raises ValueError
    assert main(["query", "bzz://nothex/demo.db", "SELECT 1"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("swarmlite: ") and "Traceback" not in err

    monkeypatch.setenv("SWARMLITE_DEBUG", "1")
    with pytest.raises(ValueError):
        main(["query", "bzz://nothex/demo.db", "SELECT 1"])
