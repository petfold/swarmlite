"""CLI-side stamp lifecycle: buy, monitor, renew.

The mechanics live in swarmfs (>= 0.4.0), next to the rest of the stamp
story: ``StampManager.plan`` sizes a batch from bee's own erasure tables
and a bucket-overflow risk target, ``buy`` purchases and polls until
usable without ever losing a bought batch id, ``plan_topup``/``topup``
and ``plan_dilute``/``dilute`` renew one, and ``buckets`` reports the
true per-bucket headroom. This module keeps only what is policy/UX: TTL
parsing and the sync bridge the CLI calls, since swarmfs is async and
swarmlite is not. Deciding to spend the wallet's xBZZ — and asking the
human first — happens in ``cli``.

A publication's life is its batch's life, so renewal is a swarmlite
concern even though the mechanics are not: a root stops being served
when its stamp expires, and an expired batch cannot be revived.
"""

from __future__ import annotations

import fsspec
from fsspec.asyn import sync

from swarmfs.stamps import (  # noqa: F401 — re-exported
    BatchPlan,
    BucketStats,
    DilutePlan,
    StampInfo,
    TopupPlan,
    depth_for_addresses,
    stamped_chunks,
    suggest_depth,
)

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}


def parse_ttl(text: str) -> int:
    """'90m', '36h', '7d', '4w' or plain seconds -> seconds."""
    text = text.strip().lower()
    unit = _UNITS.get(text[-1:], None)
    digits = text[:-1] if unit else text
    if unit is None:
        unit = 1
    try:
        value = int(digits)
        if value <= 0:
            raise ValueError
    except ValueError:
        raise ValueError(
            f"cannot parse TTL {text!r} — use e.g. '36h', '7d', '4w', "
            "or plain seconds"
        ) from None
    return value * unit


def _manager(api_url: str | None):
    from swarmfs.stamps import StampManager

    fs = fsspec.filesystem("bzz", **({"api_url": api_url} if api_url else {}))
    return fs, StampManager(fs.client)


def plan_batch(size_bytes: int, ttl_secs: int, api_url: str | None = None,
               **sizing) -> BatchPlan:
    """Price a batch for ``size_bytes`` lasting ``ttl_secs`` (swarmfs
    does the math; see StampManager.plan).

    ``sizing`` forwards ``redundancy``/``encrypted``/``risk``/``depth``.
    The defaults match how swarmlite actually uploads (swarmfs writes
    erasure level 2, unencrypted), so leaving them alone is right for
    ``publish``; pass ``depth=depth_for_addresses(...)`` for an exact,
    zero-risk size when the chunk addresses are already known.
    """
    fs, mgr = _manager(api_url)
    return sync(fs.loop, mgr.plan, size_bytes, ttl_secs, **sizing)


def buy_batch(api_url: str | None, amount: int, depth: int) -> str:
    """Buy a batch and wait until usable; returns the batch id (swarmfs
    does the purchase; see StampManager.buy)."""
    fs, mgr = _manager(api_url)
    return sync(fs.loop, mgr.buy, amount, depth)


def list_batches(api_url: str | None = None) -> list[StampInfo]:
    """Every batch the node knows. Pair with ``StampInfo.problem(min_ttl)``
    to turn "still usable" into "needs renewing"."""
    fs, mgr = _manager(api_url)
    return sync(fs.loop, mgr.list_batches)


def batch_buckets(api_url: str | None, batch_id: str) -> BucketStats:
    """The batch's true per-bucket occupancy — what bounds another upload."""
    fs, mgr = _manager(api_url)
    return sync(fs.loop, mgr.buckets, batch_id)


def plan_topup(api_url: str | None, batch_id: str, **target) -> TopupPlan:
    """Price an extension. Exactly one of ``ttl_secs`` (extend by),
    ``total_ttl_secs`` (extend to) or ``budget_bzz`` (spend at most)."""
    fs, mgr = _manager(api_url)
    return sync(fs.loop, mgr.plan_topup, batch_id, **target)


def topup_batch(api_url: str | None, batch_id: str, added_amount: int) -> StampInfo:
    """Extend a batch and wait until the node applies it (~40-50 s live).
    Spends the node wallet's xBZZ — the caller confirms first."""
    fs, mgr = _manager(api_url)
    return sync(fs.loop, mgr.topup, batch_id, added_amount)


def plan_dilute(api_url: str | None, batch_id: str, to_depth: int) -> DilutePlan:
    """Price a capacity increase in the currency it costs: remaining TTL."""
    fs, mgr = _manager(api_url)
    return sync(fs.loop, mgr.plan_dilute, batch_id, to_depth)


def dilute_batch(api_url: str | None, batch_id: str, to_depth: int) -> StampInfo:
    """Raise a batch's depth and wait until the node applies it. Costs gas
    and roughly halves the remaining TTL per depth step."""
    fs, mgr = _manager(api_url)
    return sync(fs.loop, mgr.dilute, batch_id, to_depth)
