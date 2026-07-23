"""v0 tests: correctness AND the read budget.

The read-budget assertions are the product: a point lookup on a
multi-thousand-page database must fetch a handful of pages, not the file.
Databases are built locally with stdlib sqlite3, then served through
fsspec's memory:// filesystem behind the very same VFS code path used for
bzz:// — swarmlite is transport-agnostic by design.
"""

import sqlite3
import uuid

import apsw
import fsspec
import pytest

import swarmlite

PAGE_TOTAL = 4096


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def build_db(tmp_path, *, rows=0, page_size=4096, fts=False, big_text=False):
    """Build a DELETE-journal SQLite file locally; return its bytes."""
    path = tmp_path / f"{uuid.uuid4().hex}.db"
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA page_size={page_size}")
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute(
        "CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT, val REAL, pad TEXT)"
    )
    con.execute("CREATE INDEX t_val ON t(val)")
    if rows:
        pad = "x" * (400 if big_text else 40)
        con.executemany(
            "INSERT INTO t VALUES(?,?,?,?)",
            ((i, f"name-{i}", (i * 37) % 1000 + i / 1e6, pad) for i in range(rows)),
        )
    if fts:
        con.execute("CREATE VIRTUAL TABLE ft USING fts5(body)")
        con.executemany(
            "INSERT INTO ft VALUES(?)",
            ((f"document {i} about swarm bees and sqlite pages",) for i in range(rows or 100)),
        )
    con.execute("ANALYZE")
    con.commit()
    con.execute("VACUUM")
    con.close()
    return path.read_bytes()


def serve(data: bytes) -> str:
    """Put bytes into the memory:// filesystem; return a swarmlite URL."""
    name = f"/swarmlite-test/{uuid.uuid4().hex}.db"
    fsspec.filesystem("memory").pipe_file(name, data)
    return f"memory://{name.lstrip('/')}"


def fts5_available() -> bool:
    con = apsw.Connection(":memory:")
    try:
        con.execute("CREATE VIRTUAL TABLE f USING fts5(x)")
        return True
    except apsw.SQLError:
        return False
    finally:
        con.close()


# ---------------------------------------------------------------------------
# correctness
# ---------------------------------------------------------------------------

def test_basic_select(tmp_path):
    data = build_db(tmp_path, rows=100)
    con = swarmlite.connect(serve(data))
    assert list(con.execute("SELECT name FROM t WHERE id=42")) == [("name-42",)]
    assert list(con.execute("SELECT count(*) FROM t")) == [(100,)]
    con.close()


def test_local_file_url(tmp_path):
    data = build_db(tmp_path, rows=10)
    p = tmp_path / "served.db"
    p.write_bytes(data)
    con = swarmlite.connect(f"file://{p}")
    assert list(con.execute("SELECT count(*) FROM t")) == [(10,)]
    con.close()


def test_readonly_enforced(tmp_path):
    con = swarmlite.connect(serve(build_db(tmp_path, rows=5)))
    with pytest.raises(apsw.ReadOnlyError):
        con.execute("INSERT INTO t VALUES(99,'nope',0,'')")
    with pytest.raises(apsw.ReadOnlyError):
        con.execute("CREATE TABLE nope(x)")
    con.close()


def test_empty_db(tmp_path):
    con = swarmlite.connect(serve(build_db(tmp_path, rows=0)))
    assert list(con.execute("SELECT * FROM t")) == []
    con.close()


def test_order_by_spills_to_memory_not_tempfile(tmp_path):
    # a fat unindexed sort would want temp storage; must not try to open
    # a temp file on the read-only VFS
    con = swarmlite.connect(serve(build_db(tmp_path, rows=3000, big_text=True)))
    rows = list(con.execute("SELECT id FROM t ORDER BY pad, name LIMIT 3"))
    assert len(rows) == 3
    con.close()


def test_nonurl_rejected():
    with pytest.raises(ValueError):
        swarmlite.connect("plain/path.db")


def test_truncated_file_fails_loudly(tmp_path):
    data = build_db(tmp_path, rows=5000)
    con = swarmlite.connect(serve(data[: len(data) // 2]))
    with pytest.raises(apsw.Error):
        # touches pages beyond the truncation point
        list(con.execute("SELECT count(*), max(pad) FROM t"))
    con.close()


def test_page_size_mismatch_still_works(tmp_path):
    data = build_db(tmp_path, rows=500, page_size=8192)
    con = swarmlite.connect(serve(data))
    assert list(con.execute("SELECT name FROM t WHERE id=7")) == [("name-7",)]
    con.close()


@pytest.mark.skipif(not fts5_available(), reason="apsw built without FTS5")
def test_fts5_over_lazy_vfs(tmp_path):
    data = build_db(tmp_path, rows=2000, fts=True)
    con = swarmlite.connect(serve(data))
    hits = list(con.execute(
        "SELECT rowid FROM ft WHERE ft MATCH 'swarm' LIMIT 5"))
    assert len(hits) == 5
    con.close()


def test_cache_eviction_correctness(tmp_path):
    # cache far smaller than the file: pages get evicted and refetched,
    # results must stay correct
    data = build_db(tmp_path, rows=5000, big_text=True)
    con = swarmlite.connect(serve(data), cache_pages=4)
    assert list(con.execute("SELECT count(*) FROM t")) == [(5000,)]
    assert list(con.execute("SELECT name FROM t WHERE id=4321")) == [("name-4321",)]
    con.close()


# ---------------------------------------------------------------------------
# the read budget — the product claim
# ---------------------------------------------------------------------------

def test_point_lookup_read_budget(tmp_path):
    # ~10 MB, ~2500 pages
    data = build_db(tmp_path, rows=20000, big_text=True)
    assert len(data) > 8 * 2**20
    con = swarmlite.connect(serve(data))
    f = con.swarmlite_file

    assert list(con.execute("SELECT name FROM t WHERE id=12345")) == [("name-12345",)]
    cold_reads, cold_bytes = f.read_count, f.bytes_fetched

    # cold: a handful of B-tree pages, not the file
    assert cold_reads <= 6, f.stats()
    assert cold_bytes <= 16 * PAGE_TOTAL, f.stats()

    # warm: same query again — served entirely from cache
    assert list(con.execute("SELECT name FROM t WHERE id=12345")) == [("name-12345",)]
    assert f.read_count == cold_reads, f.stats()

    # a different key shares the hot upper levels: at most ~2 new fetches
    list(con.execute("SELECT name FROM t WHERE id=17"))
    assert f.read_count <= cold_reads + 3, f.stats()
    con.close()


def test_indexed_topn_read_budget(tmp_path):
    data = build_db(tmp_path, rows=20000, big_text=True)
    con = swarmlite.connect(serve(data))
    f = con.swarmlite_file
    rows = list(con.execute(
        "SELECT id FROM t ORDER BY val DESC LIMIT 10"))
    assert len(rows) == 10
    # index walk + 10 row fetches, far below the ~2500 pages in the file
    assert f.pages_fetched <= 60, f.stats()
    con.close()


def test_full_scan_fetches_in_runs_not_per_page(tmp_path):
    # linear scan: contiguous-run fetching keeps backend calls far below
    # the page count (memory:// serves whole runs in one read)
    data = build_db(tmp_path, rows=20000, big_text=True)
    pages = len(data) // PAGE_TOTAL
    con = swarmlite.connect(serve(data))
    f = con.swarmlite_file
    assert list(con.execute("SELECT count(*) FROM t")) == [(20000,)]
    assert f.read_count < pages / 4, f.stats()
    con.close()


def test_stats_surface(tmp_path):
    con = swarmlite.connect(serve(build_db(tmp_path, rows=100)))
    list(con.execute("SELECT * FROM t WHERE id=1"))
    s = con.swarmlite_file.stats()
    assert s["file_size"] > 0
    assert s["read_count"] >= 1
    assert s["bytes_fetched"] >= PAGE_TOTAL
    assert con.swarmlite_url.startswith("memory://")
    con.close()
