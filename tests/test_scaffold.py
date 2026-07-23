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
