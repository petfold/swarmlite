"""Scaffold-stage tests: the package imports, the CLI parses, and the
not-yet-implemented surfaces fail loudly (never silently succeed).

Real tests arrive with v0 — see docs/roadmap.md. The important one to
write first: the read-budget test (build a DB, serve it via memory://
fsspec through the VFS, assert a point lookup stays within N fetches).
"""

import pytest

import swarmlite
from swarmlite.cli import main


def test_exports():
    assert callable(swarmlite.connect)
    assert callable(swarmlite.publish)
    assert swarmlite.__version__


def test_connect_is_unimplemented_not_silent():
    with pytest.raises(NotImplementedError):
        swarmlite.connect("bzz://" + "0" * 64 + "/site.db")


def test_publish_is_unimplemented_not_silent(tmp_path):
    db = tmp_path / "x.db"
    db.write_bytes(b"")
    with pytest.raises(NotImplementedError):
        swarmlite.publish(str(db))


def test_cli_help_parses():
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_cli_requires_command():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code != 0
