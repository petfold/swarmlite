"""CLI-side stamp purchase flow: TTL parsing and the --buy UX. The
mechanics (sizing tiers, pricing, purchase, polling) live in swarmfs
and are tested there (tests/test_stamps.py in the swarmfs repo)."""

import pytest

from swarmlite import stamps
from swarmlite.cli import main

MB = 2**20


def test_parse_ttl():
    assert stamps.parse_ttl("90m") == 5400
    assert stamps.parse_ttl("36h") == 36 * 3600
    assert stamps.parse_ttl("7d") == 7 * 86400
    assert stamps.parse_ttl("4w") == 28 * 86400
    assert stamps.parse_ttl("3600") == 3600
    for bad in ("", "h", "-2d", "1.5d", "soon"):
        with pytest.raises(ValueError, match="cannot parse TTL"):
            stamps.parse_ttl(bad)


def test_mechanics_are_swarmfs_reexports():
    # the sizing/pricing/purchase logic must have exactly one home
    import swarmfs.stamps

    assert stamps.BatchPlan is swarmfs.stamps.BatchPlan
    assert stamps.suggest_depth is swarmfs.stamps.suggest_depth


def test_cli_buy_flow(monkeypatch, tmp_path, capsys):
    db = tmp_path / "site.db"
    db.write_bytes(b"\0" * MB)
    monkeypatch.setattr(stamps, "plan_batch", lambda size, ttl, api: stamps.BatchPlan(
        depth=18, amount=17280000, ttl_secs=86400, cost_bzz=0.0154,
    ))
    monkeypatch.setattr(stamps, "buy_batch", lambda api, amount, depth: "ef" * 32)

    seen = {}
    import sys as _sys

    import swarmlite.publish  # noqa: F401 — ensure the module is loaded

    def fake_publish(db_path, **kw):
        seen.update(kw)
        return "00" * 32

    # the package __init__ shadows the submodule with the function of
    # the same name, so patch the module object from sys.modules
    monkeypatch.setattr(_sys.modules["swarmlite.publish"], "publish", fake_publish)
    assert main(["publish", str(db), "--buy", "--yes"]) == 0
    assert seen["stamp"] == "ef" * 32
    err = capsys.readouterr().err
    assert "0.0154 xBZZ" in err and "bought batch" in err


def test_cli_buy_conflicts_and_confirmation(monkeypatch, tmp_path, capsys):
    db = tmp_path / "site.db"
    db.write_bytes(b"\0")

    assert main(["publish", str(db), "--buy", "--stamp", "ab" * 32]) == 1
    assert "mutually exclusive" in capsys.readouterr().err

    # non-interactive stdin without --yes must refuse, not hang
    monkeypatch.setattr(stamps, "plan_batch", lambda size, ttl, api: stamps.BatchPlan(
        depth=18, amount=1, ttl_secs=86400, cost_bzz=0.1,
    ))
    assert main(["publish", str(db), "--buy"]) == 1
    assert "--yes" in capsys.readouterr().err
