"""v1 offline tests: the prepare checklist and publish orchestration.

Everything Swarm-touching is faked here; the live round-trip lives in
test_publish_live.py behind env vars.
"""

import importlib
import sqlite3
import types

import pytest

import swarmlite
from swarmlite.publish import PublishError, prepare

# `swarmlite.publish` the attribute is the function (rebound in
# __init__); fetch the module itself for monkeypatching
publish_mod = importlib.import_module("swarmlite.publish")


def patch_fs(monkeypatch, factory):
    monkeypatch.setattr(
        publish_mod, "fsspec", types.SimpleNamespace(filesystem=factory)
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def make_db(path, *, page_size=4096, wal=False, rows=0, index=True):
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA page_size={page_size}")
    con.execute(f"PRAGMA journal_mode={'WAL' if wal else 'DELETE'}")
    con.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, val REAL, pad TEXT)")
    if index:
        con.execute("CREATE INDEX t_val ON t(val)")
    if rows:
        con.executemany(
            "INSERT INTO t VALUES(?,?,?)",
            ((i, i * 0.5, "x" * 30) for i in range(rows)),
        )
    con.commit()
    con.close()
    return path


class FakeFS:
    """Records swarmfs calls; enough surface for publish()."""

    class _Tx:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def __init__(self):
        self.transaction = self._Tx()
        self.put_calls = []

    def put_file(self, lpath, rpath):
        self.put_calls.append((lpath, rpath))

    def latest(self, ref):
        return "f" * 64


# ---------------------------------------------------------------------------
# prepare: the checklist
# ---------------------------------------------------------------------------

def test_prepare_fixes_wal_and_page_size_and_warns(tmp_path):
    src = make_db(tmp_path / "src.db", page_size=8192, wal=True, rows=10)
    out = tmp_path / "out.db"
    warnings = prepare(src, out)

    joined = "\n".join(warnings)
    assert "WAL" in joined
    assert "page_size was 8192" in joined

    con = sqlite3.connect(out)
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert con.execute("PRAGMA page_size").fetchone()[0] == 4096
    assert con.execute("SELECT count(*) FROM t").fetchone() == (10,)
    con.close()


def test_prepare_clean_db_no_warnings(tmp_path):
    src = make_db(tmp_path / "src.db", rows=10)
    assert prepare(src, tmp_path / "out.db") == []


def test_prepare_warns_on_large_unindexed_table(tmp_path):
    src = make_db(tmp_path / "src.db", rows=6000, index=False)
    warnings = prepare(src, tmp_path / "out.db")
    assert any("no index" in w for w in warnings)


def test_prepare_never_touches_source(tmp_path):
    src = make_db(tmp_path / "src.db", page_size=8192, wal=True, rows=10)
    before = src.read_bytes()
    prepare(src, tmp_path / "out.db")
    assert src.read_bytes() == before


def test_prepare_rejects_corrupt_db(tmp_path):
    src = make_db(tmp_path / "src.db", rows=100)
    data = bytearray(src.read_bytes())
    data[0:16] = b"not a database!!"  # stomp the SQLite header magic
    bad = tmp_path / "bad.db"
    bad.write_bytes(bytes(data))
    with pytest.raises((PublishError, sqlite3.DatabaseError)):
        prepare(bad, tmp_path / "out.db")


def test_prepare_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        prepare(tmp_path / "nope.db", tmp_path / "out.db")


# ---------------------------------------------------------------------------
# publish orchestration (fake swarmfs)
# ---------------------------------------------------------------------------

def test_publish_pin_path(tmp_path, monkeypatch, capsys):
    fake = FakeFS()
    patch_fs(
        monkeypatch,
        lambda proto, **kw: fake if proto == "bzz" else pytest.fail(proto),
    )
    src = make_db(tmp_path / "src.db", rows=10)
    root = swarmlite.publish(src, name="demo.db")

    assert root == "f" * 64
    assert len(fake.put_calls) == 1
    lpath, rpath = fake.put_calls[0]
    assert rpath == "bzz://new/demo.db"
    assert "pin:  bzz://" + "f" * 64 + "/demo.db" in capsys.readouterr().err


def test_publish_feed_path_derives_owner(tmp_path, monkeypatch, capsys):
    seen = {}

    def fake_filesystem(proto, **kw):
        seen["proto"], seen["kw"] = proto, kw
        return FakeFS()

    patch_fs(monkeypatch, fake_filesystem)
    key = "11" * 32  # throwaway secp256k1 key
    from swarmfs.feeds import FeedSigner

    owner = FeedSigner(key).owner_hex
    src = make_db(tmp_path / "src.db", rows=10)
    root = swarmlite.publish(src, name="demo.db", feed="site", signer=key)

    assert root == "f" * 64
    assert seen["proto"] == "bzzf"
    assert seen["kw"]["signer"] == key
    err = capsys.readouterr().err
    assert f"feed: bzzf://{owner}/site/demo.db" in err


def test_publish_feed_requires_signer(tmp_path, monkeypatch):
    monkeypatch.delenv("SWARMLITE_SIGNER", raising=False)
    src = make_db(tmp_path / "src.db", rows=10)
    with pytest.raises(PublishError, match="signer"):
        swarmlite.publish(src, feed="site")


def test_publish_signer_from_env(tmp_path, monkeypatch):
    fake = FakeFS()
    patch_fs(monkeypatch, lambda proto, **kw: fake)
    monkeypatch.setenv("SWARMLITE_SIGNER", "22" * 32)
    src = make_db(tmp_path / "src.db", rows=10)
    assert swarmlite.publish(src, feed="site", quiet=True) == "f" * 64
