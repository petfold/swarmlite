"""Offline demo: lazy SELECT over a 'remote' SQLite file, no Bee needed.

Builds a ~50 MB database, serves it through fsspec's memory:// filesystem
behind the exact same VFS used for bzz://, and shows the page economy:
point lookups and FTS search fetch KBs of a 50 MB file.

Run:  python examples/offline_demo.py

For the live version, publish the file with swarmfs and replace the URL
with bzz://<root>/demo.db (see README).
"""

import sqlite3
import tempfile
import time
from pathlib import Path

import fsspec

import swarmlite

ROWS = 100_000


def build() -> bytes:
    print(f"building a demo database ({ROWS:,} posts) ...")
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "demo.db"
        con = sqlite3.connect(path)
        con.execute("PRAGMA page_size=4096")
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute(
            "CREATE TABLE posts(id INTEGER PRIMARY KEY, ts INTEGER,"
            " author TEXT, title TEXT, body TEXT)"
        )
        con.execute("CREATE INDEX posts_ts ON posts(ts)")
        words = ("swarm bee chunk feed stamp trie root query page "
                 "verifiable serverless sqlite lazy").split()
        con.executemany(
            "INSERT INTO posts VALUES(?,?,?,?,?)",
            (
                (
                    i,
                    1_700_000_000 + i * 60,
                    f"author-{i % 500}",
                    f"Post {i}: on {words[i % len(words)]}",
                    " ".join(words[(i + j) % len(words)] for j in range(60)) * 3,
                )
                for i in range(ROWS)
            ),
        )
        con.execute(
            "CREATE VIRTUAL TABLE posts_ft USING fts5(title, content=posts,"
            " content_rowid=id)"
        )
        con.execute("INSERT INTO posts_ft(posts_ft) VALUES('rebuild')")
        con.execute("ANALYZE")
        con.commit()
        con.execute("VACUUM")
        con.close()
        return path.read_bytes()


def report(con, label, t0, before):
    s = con.swarmlite_file.stats()
    pages = s["pages_fetched"] - before["pages_fetched"]
    kb = (s["bytes_fetched"] - before["bytes_fetched"]) / 1024
    reads = s["read_count"] - before["read_count"]
    print(
        f"  {label}: fetched {pages} pages ({kb:.0f} KB)"
        f" in {reads} reads"
        f" of a {s['file_size'] / 2**20:.1f} MB file"
        f"   [{(time.perf_counter() - t0) * 1000:.1f} ms]"
    )


def main():
    data = build()
    fsspec.filesystem("memory").pipe_file("/demo.db", data)
    url = "memory://demo.db"
    print(f"published {len(data) / 2**20:.1f} MB to {url}\n")

    con = swarmlite.connect(url)

    before = con.swarmlite_file.stats()
    t = time.perf_counter()
    row = next(iter(con.execute(
        "SELECT title FROM posts WHERE id = 73123")))
    print(f"point lookup -> {row[0]!r}")
    report(con, "cold", t, before)

    before = con.swarmlite_file.stats()
    t = time.perf_counter()
    next(iter(con.execute("SELECT title FROM posts WHERE id = 73123")))
    report(con, "warm (same query)", t, before)

    before = con.swarmlite_file.stats()
    t = time.perf_counter()
    rows = list(con.execute(
        "SELECT title FROM posts ORDER BY ts DESC LIMIT 5"))
    print(f"\nlatest 5 posts -> {len(rows)} rows")
    report(con, "indexed top-N", t, before)

    before = con.swarmlite_file.stats()
    t = time.perf_counter()
    rows = list(con.execute(
        "SELECT rowid FROM posts_ft WHERE posts_ft MATCH 'verifiable'"
        " LIMIT 10"))
    print(f"\nfull-text search 'verifiable' -> {len(rows)} hits")
    report(con, "FTS5", t, before)

    con.close()
    print("\nSame code path as bzz:// — swap the URL to go live.")


if __name__ == "__main__":
    main()
