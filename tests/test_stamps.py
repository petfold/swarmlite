"""CLI-side stamp lifecycle: TTL parsing, the --buy UX, and the
`swarmlite stamps` list/topup/dilute commands. The mechanics (sizing from
bee's erasure tables, pricing, purchase, renewal, polling) live in swarmfs
and are tested there (tests/test_stamps.py in the swarmfs repo)."""

import types

import pytest

from swarmlite import stamps
from swarmlite.cli import main

MB = 2**20


def _info(**kw):
    """A StampInfo with sane defaults, as the node would report one."""
    base = dict(batch_id="c9" * 32, usable=True, ttl=40 * 86400,
                utilization_ratio=0.5, label="", immutable=True, depth=19,
                amount=32954342400, bucket_depth=16, utilization=4)
    return stamps.StampInfo(**{**base, **kw})


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

    for name in ("BatchPlan", "TopupPlan", "DilutePlan", "BucketStats",
                 "StampInfo", "suggest_depth", "stamped_chunks",
                 "depth_for_addresses"):
        assert getattr(stamps, name) is getattr(swarmfs.stamps, name), name


def test_the_bridge_forwards_sizing_options():
    """plan_batch must be able to express how swarmlite will upload —
    otherwise the depth is sized for a different shape than the payload."""
    from fsspec.asyn import get_loop

    seen = {}

    class FakeMgr:
        async def plan(self, size, ttl, **kw):
            seen.update(size=size, ttl=ttl, **kw)
            return stamps.BatchPlan(
                depth=kw.get("depth", 19), amount=1, ttl_secs=ttl, cost_bzz=0.1,
                redundancy=kw.get("redundancy", 2),
                encrypted=kw.get("encrypted", False),
            )

    import swarmlite.stamps as mod

    orig = mod._manager
    mod._manager = lambda api_url=None: (types.SimpleNamespace(loop=get_loop()),
                                         FakeMgr())
    try:
        plan = mod.plan_batch(10 * MB, 86400, None, redundancy=4, encrypted=True)
        assert seen == {"size": 10 * MB, "ttl": 86400,
                        "redundancy": 4, "encrypted": True}
        assert (plan.redundancy, plan.encrypted) == (4, True)
        # an exact depth from the known chunk addresses overrides the estimate
        seen.clear()
        mod.plan_batch(10 * MB, 86400, depth=17)
        assert seen == {"size": 10 * MB, "ttl": 86400, "depth": 17}
        # and passing nothing forwards nothing, so swarmfs's defaults (which
        # match how it writes: erasure level 2, unencrypted) apply
        seen.clear()
        mod.plan_batch(10 * MB, 86400)
        assert seen == {"size": 10 * MB, "ttl": 86400}
    finally:
        mod._manager = orig


def test_cli_buy_flow(monkeypatch, tmp_path, capsys):
    db = tmp_path / "site.db"
    db.write_bytes(b"\0" * MB)
    monkeypatch.setattr(stamps, "plan_batch",
                    lambda size, ttl, api, **sizing: stamps.BatchPlan(
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
    monkeypatch.setattr(stamps, "plan_batch",
                    lambda size, ttl, api, **sizing: stamps.BatchPlan(
        depth=18, amount=1, ttl_secs=86400, cost_bzz=0.1,
    ))
    assert main(["publish", str(db), "--buy"]) == 1
    assert "--yes" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# `swarmlite stamps` — the renewal UX. A publication's life is its batch's
# life, so this is swarmlite's concern even though the mechanics are swarmfs's.
# ---------------------------------------------------------------------------


def test_stamps_list_shows_life_and_headroom(monkeypatch, capsys):
    monkeypatch.setattr(stamps, "list_batches", lambda api: [
        _info(batch_id="aa" * 32, ttl=40 * 86400),
        _info(batch_id="bb" * 32, ttl=3 * 86400, utilization=7,
              utilization_ratio=0.875),
    ])
    assert main(["stamps"]) == 0
    out = capsys.readouterr().out
    assert "aa" * 4 in out and "bb" * 4 in out
    assert "40.0 d" in out and "3.0 d" in out
    assert "4/8 (50%)" in out and "7/8 (88%)" in out
    # sorted by urgency: the batch closest to expiry first
    assert out.index("bb" * 4) < out.index("aa" * 4)
    # below the default 7d threshold, so flagged even without --check —
    # and in the table's units, not swarmfs's raw seconds
    assert "needs renewing (under 7d)" in out
    assert "3471299s" not in out and "below the minimum" not in out


def test_stamps_check_exits_nonzero_and_says_what_to_do(monkeypatch, capsys):
    monkeypatch.setattr(stamps, "list_batches", lambda api: [
        _info(batch_id="bb" * 32, ttl=3 * 86400)])
    assert main(["stamps", "--check"]) == 1
    err = capsys.readouterr().err
    assert "swarmlite stamps topup" in err and "cannot be revived" in err
    # healthy batches pass, and the threshold is tunable
    monkeypatch.setattr(stamps, "list_batches", lambda api: [_info(ttl=40 * 86400)])
    assert main(["stamps", "--check"]) == 0
    assert main(["stamps", "--check", "--min-ttl", "60d"]) == 1


def test_stamps_list_without_batches_is_an_error(monkeypatch, capsys):
    monkeypatch.setattr(stamps, "list_batches", lambda api: [])
    assert main(["stamps"]) == 1
    assert "no postage batches" in capsys.readouterr().err


def test_stamps_topup_targets(monkeypatch, capsys):
    seen = {}

    def fake_plan(api, batch, **target):
        seen.update(batch=batch, **target)
        return stamps.TopupPlan(batch_id=batch, depth=19, added_amount=49502880,
                                added_ttl_secs=3600, total_ttl_secs=40 * 86400,
                                cost_bzz=0.0026)

    monkeypatch.setattr(stamps, "plan_topup", fake_plan)
    monkeypatch.setattr(stamps, "topup_batch",
                        lambda api, batch, amount: _info(ttl=40 * 86400 + 3600))

    assert main(["stamps", "topup", "c9" * 32, "--for", "1h", "--yes"]) == 0
    assert seen == {"batch": "c9" * 32, "ttl_secs": 3600}
    err = capsys.readouterr().err
    assert "0.0026 xBZZ" in err and "+1.0 h" in err

    assert main(["stamps", "topup", "c9" * 32, "--to", "60d", "--yes"]) == 0
    assert seen["total_ttl_secs"] == 60 * 86400
    assert main(["stamps", "topup", "c9" * 32, "--budget", "0.5", "--yes"]) == 0
    assert seen["budget_bzz"] == 0.5


def test_stamps_topup_needs_a_target_and_a_confirmation(monkeypatch, capsys):
    monkeypatch.setattr(stamps, "plan_topup", lambda api, batch, **t:
                        stamps.TopupPlan(batch_id=batch, depth=19, added_amount=1,
                                         added_ttl_secs=60, total_ttl_secs=60,
                                         cost_bzz=0.1))
    spent = []
    monkeypatch.setattr(stamps, "topup_batch",
                        lambda api, batch, amount: spent.append(amount) or _info())

    # argparse enforces exactly one target
    with pytest.raises(SystemExit):
        main(["stamps", "topup", "c9" * 32])
    with pytest.raises(SystemExit):
        main(["stamps", "topup", "c9" * 32, "--for", "1h", "--budget", "1"])
    # non-interactive without --yes must refuse rather than spend or hang
    assert main(["stamps", "topup", "c9" * 32, "--for", "1h"]) == 1
    assert "--yes" in capsys.readouterr().err
    assert spent == []


def test_stamps_topup_surfaces_the_dilute_first_warning(monkeypatch, capsys):
    monkeypatch.setattr(stamps, "plan_topup", lambda api, batch, **t:
                        stamps.TopupPlan(batch_id=batch, depth=19, added_amount=1,
                                         added_ttl_secs=60, total_ttl_secs=60,
                                         cost_bzz=0.1,
                                         warning="dilute FIRST, since ..."))
    monkeypatch.setattr(stamps, "topup_batch", lambda api, batch, amount: _info())
    assert main(["stamps", "topup", "c9" * 32, "--for", "1h", "--yes"]) == 0
    assert "warning: dilute FIRST" in capsys.readouterr().err


def test_stamps_dilute_prices_it_in_ttl(monkeypatch, capsys):
    monkeypatch.setattr(stamps, "plan_dilute", lambda api, batch, depth:
                        stamps.DilutePlan(batch_id=batch, from_depth=19,
                                          to_depth=21, ttl_before_secs=40 * 86400,
                                          ttl_after_secs=10 * 86400))
    monkeypatch.setattr(stamps, "dilute_batch", lambda api, batch, depth:
                        _info(depth=21, ttl=10 * 86400))
    assert main(["stamps", "dilute", "c9" * 32, "--depth", "21", "--yes"]) == 0
    err = capsys.readouterr().err
    assert "x4" in err  # capacity per bucket quadruples over two steps
    assert "40.0 d -> 10.0 d" in err
    assert "top up to restore" in err


def test_stamps_rejects_copy_pasted_placeholders(capsys):
    assert main(["stamps", "topup", "<batchID>", "--for", "1h", "--yes"]) == 1
    assert "placeholder" in capsys.readouterr().err
