"""Stamp purchase helper: sizing, pricing, buying — all offline (the
node API is stubbed at the _get_json/_post_json seam)."""

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


def test_suggest_depth_tiers():
    assert stamps.suggest_depth(1 * MB) == 18
    assert stamps.suggest_depth(15 * MB) == 18
    assert stamps.suggest_depth(16 * MB) == 19  # 42 MB filled a depth-18 live
    assert stamps.suggest_depth(150 * MB) == 19
    assert stamps.suggest_depth(1024 * MB) == 20
    assert stamps.suggest_depth(2048 * MB) == 21  # doubling past 1 GB
    assert stamps.suggest_depth(5 * 1024 * MB) == 23


def test_plan_batch_clamps_to_minimum_validity(monkeypatch):
    monkeypatch.setattr(stamps, "_get_json", lambda url: {
        "currentPrice": "1000", "minimumValidityBlocks": 17280,
    })
    floor = 17280 + 720  # node minimum plus the 1h price-drift pad
    plan = stamps.plan_batch(10 * MB, ttl_secs=3600, api_url="http://x")
    assert plan.depth == 18
    assert plan.amount == floor * 1000  # 1h asked, padded 24h minimum wins
    assert plan.ttl_secs == floor * 5
    assert plan.cost_bzz == pytest.approx(floor * 1000 * 2**18 / 10**16)

    week = stamps.parse_ttl("7d")
    plan = stamps.plan_batch(10 * MB, ttl_secs=week, api_url="http://x")
    assert plan.amount == (week // 5) * 1000
    assert plan.ttl_secs == week


def test_buy_batch_polls_until_usable(monkeypatch):
    # first polls 400 while the purchase tx confirms (seen live), then
    # the batch appears, then becomes usable
    import urllib.error

    def not_found():
        raise urllib.error.HTTPError("http://x", 400, "Bad Request", {}, None)

    polls = iter([not_found, not_found, lambda: {"usable": False},
                  lambda: {"usable": True}])
    monkeypatch.setattr(stamps, "_post_json", lambda url: {"batchID": "ab" * 32})
    monkeypatch.setattr(stamps, "_get_json", lambda url: next(polls)())
    monkeypatch.setattr(stamps.time, "sleep", lambda s: None)
    assert stamps.buy_batch("http://x", 1000, 18) == "ab" * 32


def test_buy_batch_never_loses_the_bought_id(monkeypatch):
    import urllib.error

    monkeypatch.setattr(stamps, "_post_json", lambda url: {"batchID": "ee" * 32})

    def boom(url):
        raise urllib.error.HTTPError(url, 500, "Internal Server Error", {}, None)

    monkeypatch.setattr(stamps, "_get_json", boom)
    monkeypatch.setattr(stamps.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match=f"batch {'ee' * 32} was bought"):
        stamps.buy_batch("http://x", 1000, 18)


def test_buy_batch_timeout_is_actionable(monkeypatch):
    monkeypatch.setattr(stamps, "_post_json", lambda url: {"batchID": "cd" * 32})
    monkeypatch.setattr(stamps, "_get_json", lambda url: {"usable": False})
    monkeypatch.setattr(stamps.time, "sleep", lambda s: None)
    ticks = iter(range(0, 10_000, 100))
    monkeypatch.setattr(stamps.time, "monotonic", lambda: next(ticks))
    with pytest.raises(TimeoutError, match="retry with --stamp"):
        stamps.buy_batch("http://x", 1000, 18, wait_secs=300)


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
