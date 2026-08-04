"""Opt-in live round-trip against a real Bee node.

    SWARMLITE_TEST_BEE=http://localhost:1633 \
    SWARMLITE_TEST_STAMP=<batchID> pytest tests/test_publish_live.py -v

The feed round-trip additionally needs SWARMLITE_TEST_SIGNER (any
throwaway secp256k1 private key hex — feeds need no funds on that key,
only the postage stamp pays).
"""

import os
import sqlite3
import time
import uuid

import pytest

import swarmlite

BEE = os.environ.get("SWARMLITE_TEST_BEE")
STAMP = os.environ.get("SWARMLITE_TEST_STAMP")
SIGNER = os.environ.get("SWARMLITE_TEST_SIGNER")

pytestmark = pytest.mark.skipif(
    not (BEE and STAMP), reason="set SWARMLITE_TEST_BEE and SWARMLITE_TEST_STAMP"
)


def small_db(tmp_path):
    path = tmp_path / "live.db"
    con = sqlite3.connect(path)
    con.execute("PRAGMA page_size=4096")
    con.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT)")
    con.executemany(
        "INSERT INTO t VALUES(?,?)", ((i, f"row-{i}") for i in range(500))
    )
    con.commit()
    con.close()
    return path


def test_pin_roundtrip(tmp_path):
    root = swarmlite.publish(
        small_db(tmp_path), name="live.db", stamp=STAMP, api_url=BEE, quiet=True
    )
    con = swarmlite.connect(f"bzz://{root}/live.db", api_url=BEE)
    assert list(con.execute("SELECT name FROM t WHERE id=123")) == [("row-123",)]
    assert con.swarmlite_file.stats()["pages_fetched"] <= 8
    con.close()


@pytest.mark.skipif(not SIGNER, reason="set SWARMLITE_TEST_SIGNER for the feed test")
def test_feed_roundtrip(tmp_path):
    from swarmfs.feeds import FeedSigner

    owner = FeedSigner(SIGNER).owner_hex
    topic = f"swarmlite-test-{uuid.uuid4().hex[:8]}"
    swarmlite.publish(
        small_db(tmp_path), name="live.db", feed=topic,
        signer=SIGNER, stamp=STAMP, api_url=BEE, quiet=True,
    )
    url = f"bzzf://{owner}/{topic}/live.db"
    # Feed lookups lag freshly published updates while the SOC push-syncs
    # to its neighborhood — minutes on mainnet, not seconds.
    deadline = time.monotonic() + int(os.environ.get("SWARMLITE_TEST_FEED_WAIT", "300"))
    last = None
    while time.monotonic() < deadline:
        try:
            con = swarmlite.connect(url, api_url=BEE)
            assert list(con.execute("SELECT name FROM t WHERE id=7")) == [("row-7",)]
            con.close()
            return
        except Exception as e:  # noqa: BLE001 - retry loop reports the last error
            last = e
            time.sleep(10)
    pytest.fail(f"feed read did not become available: {last!r}")


def test_encrypted_pin_roundtrip(tmp_path):
    """Encrypted publish and recall: the 128-hex URL is the secret; the
    reader needs no flag — the node decrypts in the load path, and the
    VFS pages through ciphertext-at-rest transparently."""
    root = swarmlite.publish(
        small_db(tmp_path), name="enc.db", stamp=STAMP, api_url=BEE,
        encrypt=True, quiet=True,
    )
    assert len(root) == 128
    con = swarmlite.connect(f"bzz://{root}/enc.db", api_url=BEE)
    assert list(con.execute("SELECT name FROM t WHERE id=123")) == [("row-123",)]
    assert con.swarmlite_file.stats()["pages_fetched"] <= 8
    con.close()
