"""Postage-stamp purchase helper: size a batch for a file, price it from
the chain state, buy it through the node's owner API, wait until usable.

Buying spends real xBZZ from the node's wallet, so nothing is implicit:
the CLI computes the plan, shows the exact cost, and requires
confirmation (or ``--yes``). Node-owner endpoints only — ``POST
/stamps`` works on your own node, never on a public gateway.

Pricing (Gnosis): a batch of ``amount`` per chunk lasts about
``amount / currentPrice`` blocks of 5 s; the node refuses batches below
``minimumValidityBlocks`` (24 h at the time of writing), so the plan
clamps to that. Cost in xBZZ is ``amount * 2**depth / 10**16``.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

BLOCK_SECS = 5  # Gnosis block time
PLUR_PER_BZZ = 10**16

# file size -> batch depth, with headroom: theoretical capacity is
# 2**depth * 4 KB, but an immutable batch fails as soon as any single
# bucket fills, so small files still want depth >= 18. Matches the table
# in docs/USER_GUIDE.md §3 (validated live at depths 18-20).
_DEPTH_TIERS = ((50 * 2**20, 18), (300 * 2**20, 19), (2**30, 20))

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


def suggest_depth(size_bytes: int) -> int:
    """Smallest batch depth that holds ``size_bytes`` with headroom."""
    for limit, depth in _DEPTH_TIERS:
        if size_bytes <= limit:
            return depth
    return 20 + math.ceil(math.log2(size_bytes / 2**30))


@dataclass
class BatchPlan:
    depth: int
    amount: int
    ttl_secs: int  # actual validity after the node's minimum is applied
    cost_bzz: float


def plan_batch(size_bytes: int, ttl_secs: int, api_url: str) -> BatchPlan:
    """Price a batch for ``size_bytes`` lasting ``ttl_secs``, at the
    current on-chain price (clamped to the node's minimum validity)."""
    chain = _get_json(f"{api_url}/chainstate")
    price = int(chain["currentPrice"])
    # the node requires STRICTLY more than minimumValidityBlocks at
    # purchase time, and the price can move between planning and buying
    # (rejected live at the exact minimum) — pad the floor by an hour
    floor = int(chain.get("minimumValidityBlocks", 0)) + 3600 // BLOCK_SECS
    blocks = max(math.ceil(ttl_secs / BLOCK_SECS), floor)
    depth = suggest_depth(size_bytes)
    amount = blocks * price
    return BatchPlan(
        depth=depth,
        amount=amount,
        ttl_secs=blocks * BLOCK_SECS,
        cost_bzz=amount * 2**depth / PLUR_PER_BZZ,
    )


def buy_batch(
    api_url: str, amount: int, depth: int, *, wait_secs: int = 300
) -> str:
    """POST /stamps/{amount}/{depth}, then poll until the batch is
    usable (on-chain confirmation plus node sync; took ~40 s live).
    Returns the batch id."""
    try:
        created = _post_json(f"{api_url}/stamps/{amount}/{depth}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        if "insufficient amount" in detail:
            hint = ("the on-chain price moved between planning and "
                    "buying — retry, or ask for a longer --ttl")
        else:
            hint = (f"the node's wallet may lack xBZZ or xDAI for gas "
                    f"(check {api_url}/wallet). Fund it, or get a stamp "
                    "from a service such as Beeport and pass it with "
                    "--stamp")
        raise RuntimeError(
            f"buying the batch failed (HTTP {e.code}): {detail.strip()} — {hint}."
        ) from None
    batch_id = created["batchID"]
    # from here on the money is spent: every failure path must carry the
    # batch id, or a confirmed batch would be orphaned
    deadline = time.monotonic() + wait_secs
    while time.monotonic() < deadline:
        try:
            if _get_json(f"{api_url}/stamps/{batch_id}").get("usable"):
                return batch_id
        except urllib.error.HTTPError as e:
            # 400/404 while the purchase tx confirms: not known yet
            if e.code not in (400, 404):
                raise RuntimeError(
                    f"batch {batch_id} was bought (tx submitted) but "
                    f"polling its status failed: {e} — check "
                    f"{api_url}/stamps/{batch_id} and pass it with "
                    "--stamp once usable"
                ) from None
        time.sleep(3)
    raise TimeoutError(
        f"batch {batch_id} was bought but is still not usable after "
        f"{wait_secs}s — it may just need longer; check "
        f"{api_url}/stamps/{batch_id} and retry with --stamp once usable"
    )


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def _post_json(url: str) -> dict:
    req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)
