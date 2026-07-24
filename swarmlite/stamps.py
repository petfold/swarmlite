"""CLI-side stamp purchase flow.

The mechanics live in swarmfs, next to the rest of the stamp story
(listing, validation, auto-selection): ``StampManager.plan`` sizes and
prices a batch (bucket-overflow-aware depth tiers, chain-minimum
clamping), ``StampManager.buy`` purchases and polls until usable
without ever losing a bought batch id. This module keeps only what is
policy/UX: TTL parsing and the sync bridge the CLI calls. Deciding to
spend the wallet's xBZZ — and asking the human first — happens in
``cli._buy_stamp``.
"""

from __future__ import annotations

import fsspec
from fsspec.asyn import sync

from swarmfs.stamps import BatchPlan, suggest_depth  # noqa: F401 — re-exported

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


def plan_batch(size_bytes: int, ttl_secs: int, api_url: str | None = None) -> BatchPlan:
    """Price a batch for ``size_bytes`` lasting ``ttl_secs`` (swarmfs
    does the math; see StampManager.plan)."""
    fs, mgr = _manager(api_url)
    return sync(fs.loop, mgr.plan, size_bytes, ttl_secs)


def buy_batch(api_url: str | None, amount: int, depth: int) -> str:
    """Buy a batch and wait until usable; returns the batch id (swarmfs
    does the purchase; see StampManager.buy)."""
    fs, mgr = _manager(api_url)
    return sync(fs.loop, mgr.buy, amount, depth)
