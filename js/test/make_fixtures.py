"""Generate js/test/fixtures/verified.json USING SWARMFS — the Python
implementation is the reference, so the JS port in js/src/verify.js and
js/src/mantaray.js is tested against bytes Python produced, not against
itself.

    .venv/bin/python js/test/make_fixtures.py

Contents: a tiny SQLite database split into content-addressed chunks, a
Mantaray manifest mapping 'tiny.db' to it, and a signed feed update
(single-owner chunk) pointing at the manifest root. Everything is
deterministic (fixed key, fixed timestamp), so regenerating is a no-op
unless the formats change.
"""

import base64
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from swarmfs.bmt import CHUNK_PAYLOAD_SIZE, cac_data, chunk_address, keccak256
from swarmfs.feeds import FeedSigner, feed_identifier, soc_address, topic_bytes
from swarmfs.mantaray.node import NT_VALUE, Fork, Node, marshal

OUT = Path(__file__).parent / "fixtures" / "verified.json"
SIGNER_KEY = "ad" * 32  # throwaway, deterministic
TOPIC = "swarmlite-fixture"
TIMESTAMP = 1753344000

chunks: dict[str, bytes] = {}


def store(data: bytes) -> bytes:
    """Store one chunk, return its address."""
    addr = chunk_address(data)
    chunks[addr.hex()] = data
    return addr


def split(content: bytes) -> bytes:
    """Minimal single-level splitter (fine for < 512 KB fixtures):
    leaf chunks plus one intermediate root when needed."""
    leaves = [
        store(cac_data(content[i : i + CHUNK_PAYLOAD_SIZE]))
        for i in range(0, len(content), CHUNK_PAYLOAD_SIZE)
    ]
    if len(leaves) == 1:
        return leaves[0]
    assert len(leaves) <= 128, "fixture too large for the minimal splitter"
    root = len(content).to_bytes(8, "little") + b"".join(leaves)
    return store(root)


def build_db() -> bytes:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "tiny.db"
        con = sqlite3.connect(path)
        con.execute("PRAGMA page_size=4096")
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("CREATE TABLE posts(id INTEGER PRIMARY KEY, title TEXT)")
        con.executemany(
            "INSERT INTO posts VALUES (?, ?)",
            ((i, f"Post {i}: deterministic fixture row") for i in range(200)),
        )
        con.commit()
        con.execute("VACUUM")
        con.close()
        return path.read_bytes()


def main() -> int:
    db = build_db()
    db_ref = split(db)

    # manifest: root --"tiny.db" fork--> value node carrying the entry
    value_node = Node(entry=db_ref, ref_bytes_size=32)
    value_ref = store(cac_data(marshal(value_node)))
    root_node = Node(ref_bytes_size=32)
    root_node.forks[ord("t")] = Fork(
        node_type=NT_VALUE,
        prefix=b"tiny.db",
        ref=value_ref,
        metadata={"Content-Type": "application/octet-stream"},
    )
    manifest_ref = store(cac_data(marshal(root_node)))

    # feed update: SOC signed by the fixture key, pointing at the manifest
    signer = FeedSigner(SIGNER_KEY)
    topic = topic_bytes(TOPIC)
    identifier = feed_identifier(topic, 0)
    update = cac_data(TIMESTAMP.to_bytes(8, "big") + manifest_ref)
    digest = keccak256(identifier + chunk_address(update))
    soc = identifier + signer.sign_digest(digest) + update
    soc_addr = soc_address(identifier, signer.owner)

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "db": {
            "ref": db_ref.hex(),
            "size": len(db),
            "content": base64.b64encode(db).decode(),
        },
        "manifest": {"root": manifest_ref.hex(), "path": "tiny.db"},
        "feed": {
            "owner": signer.owner_hex,
            "topic": TOPIC,
            "index": 0,
            "socAddress": soc_addr.hex(),
            "soc": base64.b64encode(soc).decode(),
            "reference": manifest_ref.hex(),
        },
        "chunks": {ref: base64.b64encode(data).decode() for ref, data in chunks.items()},
    }, indent=1))
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {len(chunks)} chunks, "
          f"db {len(db)} B, manifest root {manifest_ref.hex()[:16]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
