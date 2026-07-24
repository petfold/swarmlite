"""Feed history: every update of a feed is a (timestamp, root) pair, so
a feed IS the list of snapshots ever published to it. This module
enumerates them.

Sequence feeds advance strictly by one (swarmfs publishes index
``latest + 1``), so history is a walk over indices ``0 .. latest``: the
single-owner chunk of every past update stays retrievable at its
derived address — nothing extra needs to be stored at publish time.

Transport stays swarmfs's: the feed head lookup and chunk fetches go
through the same client the ``bzzf://`` filesystem uses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

import fsspec
from fsspec.asyn import sync

_URL = re.compile(
    r"^(?:bzzf://)?(?:0x)?([0-9a-fA-F]{40})/([^/]+)(?:/(.+))?$"
)


@dataclass
class Snapshot:
    """One published version: ``bzz://<root>/...`` pins it forever."""

    index: int
    root: str | None  # None: update exists but is not retrievable (yet)
    timestamp: datetime | None  # from the bee-js update payload


def parse_feed_url(url: str) -> tuple[str, str, str | None]:
    """Split ``bzzf://<owner>/<topic>[/<name>]`` -> (owner, topic, name)."""
    m = _URL.match(url)
    if not m:
        raise ValueError(
            f"cannot parse feed URL {url!r} — expected "
            "bzzf://<40-hex owner>/<topic>[/<file>]"
        )
    return m.group(1).lower(), m.group(2), m.group(3)


def snapshots(url: str, *, verify: bool = False, **storage_options) -> list[Snapshot]:
    """All updates of a feed, oldest first (the last one is what
    ``bzzf://`` resolves to today). With ``verify=True`` every update's
    single-owner chunk is signature-checked (needs swarmfs[feeds])."""
    from swarmfs.feeds import owner_bytes, topic_bytes

    owner, topic, _ = parse_feed_url(url)
    fs = fsspec.filesystem("bzzf", **storage_options)
    return sync(
        fs.loop, _history, fs.client, owner_bytes(owner), topic_bytes(topic), verify
    )


async def _history(client, owner: bytes, topic: bytes, verify: bool) -> list[Snapshot]:
    from swarmfs.feeds import FeedError, feed_identifier, soc_address

    head = await client.feed_head(owner.hex(), topic.hex())
    if head is None:
        return []
    latest = int.from_bytes(bytes.fromhex(head[0]), "big")

    out: list[Snapshot] = []
    for index in range(latest + 1):
        address = soc_address(feed_identifier(topic, index), owner)
        try:
            soc = await client.chunk_get(address.hex())
        except FileNotFoundError:
            # published but not currently retrievable (syncing/expired)
            out.append(Snapshot(index=index, root=None, timestamp=None))
            continue
        if verify:
            from swarmfs.feeds import verify_soc

            verify_soc(soc, owner, address)
        try:
            out.append(_decode(index, soc))
        except FeedError as e:
            raise FeedError(f"feed update {index}: {e}") from None
    return out


def _decode(index: int, soc: bytes) -> Snapshot:
    """Extract (timestamp, root) from a single-owner chunk's payload."""
    from swarmfs.feeds import SOC_PAYLOAD_OFFSET, FeedError

    payload = soc[SOC_PAYLOAD_OFFSET:]
    if len(payload) in (40, 72):  # bee-js format: timestamp(8) + ref
        ts = int.from_bytes(payload[:8], "big")
        return Snapshot(
            index=index,
            root=payload[8:].hex(),
            timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
        )
    if len(payload) in (32, 64):  # bare reference, no timestamp
        return Snapshot(index=index, root=payload.hex(), timestamp=None)
    raise FeedError(
        f"unexpected {len(payload)}-byte update payload — not a bee-js "
        "feed update (timestamp + reference)"
    )
