"""Feed-history tests, offline: single-owner chunks are crafted with
swarmfs's own signer and served by a fake client."""

import asyncio
import importlib
from datetime import timezone

import pytest

from swarmlite.cli import main
from swarmlite.snapshots import Snapshot, parse_feed_url

# `swarmlite.snapshots` the attribute is the function (rebound in
# __init__); fetch the module itself for _history/monkeypatching
snap_mod = importlib.import_module("swarmlite.snapshots")

from swarmfs.bmt import cac_data, chunk_address, keccak256
from swarmfs.feeds import FeedSigner, feed_identifier, soc_address, topic_bytes

KEY = "5e" * 32
TOPIC = "history-test"


def make_soc(signer, topic: bytes, index: int, root: bytes, ts: int) -> tuple[str, bytes]:
    """(address hex, chunk bytes) of one bee-js feed update."""
    data = cac_data(ts.to_bytes(8, "big") + root)
    identifier = feed_identifier(topic, index)
    sig = signer.sign_digest(keccak256(identifier + chunk_address(data)))
    return soc_address(identifier, signer.owner).hex(), identifier + sig + data


class FakeClient:
    def __init__(self, latest_index: int, chunks: dict[str, bytes]):
        self._latest = latest_index
        self._chunks = chunks

    async def feed_head(self, owner: str, topic: str):
        return (self._latest.to_bytes(8, "big").hex(), "")

    async def chunk_get(self, ref: str) -> bytes:
        if ref not in self._chunks:
            raise FileNotFoundError(ref)
        return self._chunks[ref]


def build_history(n=3, missing=()):
    signer = FeedSigner(KEY)
    topic = topic_bytes(TOPIC)
    chunks, roots = {}, []
    for i in range(n):
        root = bytes([i]) * 32
        roots.append(root.hex())
        addr, soc = make_soc(signer, topic, i, root, ts=1_753_300_000 + i * 1000)
        if i not in missing:
            chunks[addr] = soc
    return signer, topic, FakeClient(n - 1, chunks), roots


def test_parse_feed_url():
    owner = "ab" * 20
    assert parse_feed_url(f"bzzf://{owner}/mysite/demo.db") == (owner, "mysite", "demo.db")
    assert parse_feed_url(f"0x{owner.upper()}/mysite") == (owner, "mysite", None)
    with pytest.raises(ValueError, match="cannot parse feed URL"):
        parse_feed_url("bzz://tooshort/x")


def test_history_walks_all_indices_oldest_first():
    signer, topic, client, roots = build_history(3)
    snaps = asyncio.run(snap_mod._history(client, signer.owner, topic, verify=True))
    assert [s.index for s in snaps] == [0, 1, 2]
    assert [s.root for s in snaps] == roots
    assert snaps[0].timestamp.tzinfo == timezone.utc
    assert snaps[1].timestamp.timestamp() == 1_753_301_000


def test_history_marks_unretrievable_updates():
    signer, topic, client, roots = build_history(3, missing={1})
    snaps = asyncio.run(snap_mod._history(client, signer.owner, topic, verify=False))
    assert snaps[1] == Snapshot(index=1, root=None, timestamp=None)
    assert snaps[0].root == roots[0] and snaps[2].root == roots[2]


def test_history_verify_catches_wrong_owner():
    from swarmfs.join import VerificationError

    signer = FeedSigner(KEY)
    other = FeedSigner("6f" * 32)
    topic = topic_bytes(TOPIC)
    _, soc = make_soc(signer, topic, 0, b"\x01" * 32, ts=1)
    # plant the genuine update at the address derived for a DIFFERENT
    # owner: address check passes, signature recovery must expose it
    fake_addr = soc_address(feed_identifier(topic, 0), other.owner).hex()
    client = FakeClient(0, {fake_addr: soc})
    with pytest.raises(VerificationError):
        asyncio.run(snap_mod._history(client, other.owner, topic, verify=True))


def test_cli_snapshots_listing(monkeypatch, capsys):
    from datetime import datetime

    owner = "ab" * 20
    monkeypatch.setattr(snap_mod, "snapshots", lambda url, **kw: [
        Snapshot(0, "aa" * 32, datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)),
        Snapshot(1, None, None),
        Snapshot(2, "cc" * 32, datetime(2026, 7, 24, 9, 0, tzinfo=timezone.utc)),
    ])
    assert main(["snapshots", f"bzzf://{owner}/mysite/demo.db"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("0\t2026-07-24 08:00:00 UTC\tbzz://" + "aa" * 32 + "/demo.db")
    assert "(not retrievable yet)" in out[1]
    assert out[2].endswith("<- latest") and "/demo.db" in out[2]
