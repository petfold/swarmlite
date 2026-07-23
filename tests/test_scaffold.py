"""Package-surface tests: exports, CLI parsing, and loud failure of the
not-yet-implemented publisher (v1). The real v0 tests live in test_vfs.py.
"""

import pytest

import swarmlite
from swarmlite.cli import main


def test_exports():
    assert callable(swarmlite.connect)
    assert callable(swarmlite.publish)
    assert swarmlite.__version__


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
