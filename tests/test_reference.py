"""docs/REFERENCE.md is pinned to the code: if a name, a parameter, or the
export list in that file and the package disagree, this suite fails.
(Same pattern as swarmfs, recordstore, ontodag and ontodag-fs.)"""

import importlib
import inspect
import re
from pathlib import Path

import swarmlite

DOC = Path(__file__).parent.parent / "docs" / "REFERENCE.md"
TEXT = DOC.read_text(encoding="utf-8")


def _table_rows(section: str) -> list[list[str]]:
    m = re.search(rf"^## {re.escape(section)}.*?(?=^## |\Z)", TEXT,
                  re.M | re.S)
    assert m, f"section {section!r} missing from REFERENCE.md"
    rows = []
    for line in m.group(0).splitlines():
        if line.startswith("|") and not re.match(r"^\|[\s\-|]+\|$", line):
            rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows[1:] if rows else []


def _first_code(cell: str) -> str | None:
    m = re.match(r"`([^`]+)`", cell)
    return m.group(1) if m else None


def _resolve(dotted: str):
    parts = dotted.split(".")
    if parts[0] == "stamps":
        obj = importlib.import_module("swarmlite.stamps")
        parts = parts[1:]
    else:
        obj = swarmlite
    for part in parts:
        obj = getattr(obj, part)
    return obj


API_SECTIONS = ["4. Reading", "5. Publishing", "6. Stamps (`swarmlite.stamps`)"]


def _api_rows():
    for section in API_SECTIONS:
        for row in _table_rows(section):
            name = _first_code(row[0])
            if name and re.fullmatch(r"[A-Za-z_][\w.]*", name):
                yield section, name, row


def test_exports_table_is_exactly_dunder_all():
    documented = {
        _first_code(row[0])
        for row in _table_rows("3. Exports")
        if _first_code(row[0])
    }
    expected = set(swarmlite.__all__) - {"__version__"}
    assert documented == expected, (
        f"only in docs: {documented - expected}; "
        f"only in __all__: {expected - documented}")


def test_every_documented_name_resolves():
    checked = 0
    for section, name, _ in _api_rows():
        try:
            _resolve(name)
        except AttributeError as e:
            raise AssertionError(
                f"{section}: `{name}` does not resolve: {e}") from None
        checked += 1
    assert checked >= 11


def test_documented_parameters_exist():
    checked = 0
    for section, name, row in _api_rows():
        sig_cell = _first_code(row[1]) if len(row) > 1 else None
        if not sig_cell or not sig_cell.startswith("("):
            continue
        obj = _resolve(name)
        target = obj.__init__ if inspect.isclass(obj) else obj
        try:
            params = inspect.signature(target).parameters
        except (ValueError, TypeError):
            continue
        real = set(params) | {"self"}
        has_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD
                         for p in params.values())
        for chunk in sig_cell.strip("()").split(","):
            param = re.split(r"[=:]", chunk.strip())[0].strip("* ")
            if not re.fullmatch(r"[A-Za-z_]\w*", param):
                continue
            assert has_kwargs or param in real, (
                f"{section}: `{name}` documents parameter {param!r} "
                f"which the code does not have (real: {sorted(real)})")
            checked += 1
    assert checked >= 20


def test_snapshot_fields_documented():
    for field in swarmlite.Snapshot.__dataclass_fields__:
        assert f"`{field}`" in TEXT, (
            f"Snapshot field {field!r} missing from REFERENCE.md")


def test_cli_commands_documented():
    from swarmlite import cli
    source = open(cli.__file__).read()
    for command in set(re.findall(r'add_parser\(\s*"([a-z]+)"', source)):
        # nested subcommands appear as e.g. `stamps topup ID`
        assert re.search(rf"\| `[^`]*\b{re.escape(command)}\b", TEXT), (
            f"CLI command {command!r} missing from the REFERENCE.md CLI table")


def test_described_version_matches_pyproject():
    doc_version = re.search(
        r"version this file describes: `([\d.]+)`", TEXT).group(1)
    pyproject = (Path(__file__).parent.parent / "pyproject.toml").read_text()
    real = re.search(r'^version = "([\d.]+)"', pyproject, re.M).group(1)
    assert doc_version == real, (
        f"REFERENCE.md describes {doc_version}, pyproject says {real} — "
        "update the reference as part of the release docs sweep")
