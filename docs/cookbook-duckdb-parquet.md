# Cookbook: DuckDB + Parquet on Swarm — the analytics flavour

swarmlite covers the OLTP-ish shape (point lookups, search) with
SQLite. For **analytics** — scans, aggregates, GROUP BY over millions
of rows — the right artifact is Parquet queried by DuckDB, and it works
on Swarm today with zero new code: swarmfs is an fsspec filesystem, and
DuckDB speaks fsspec.

Everything below was run for real: 1 M rows published to Gnosis
mainnet, then queried lazily.

## 1. Publish a Parquet file

```python
import fsspec
import pandas as pd

df = ...  # your 1M-row dataframe

fs = fsspec.filesystem("bzz", stamp="auto")
with fs.transaction:
    with fs.open("bzz://new/sales.parquet", "wb") as f:
        df.to_parquet(f, row_group_size=50_000)
root = fs.latest("new")
```

Two publisher-side choices matter (they are Parquet's analog of
swarmlite's "add indexes"):

- **`row_group_size`** — the pruning granule. Row-group min/max
  statistics are what lets DuckDB *skip* data; 50 k–500 k rows per
  group is a good range (tiny groups bloat metadata, one giant group
  can't be pruned).
- **Sort by your filter column** before writing. Our test data was
  sorted by `day`, so a `WHERE day BETWEEN ...` touches only the row
  groups whose min/max overlap the window. Unsorted data makes every
  group's range overlap everything, and pruning dies.

Our 1 M-row table compressed to a 5.0 MB file with 20 row groups.

## 2. Query it with DuckDB

```python
import duckdb
import fsspec

fs = fsspec.filesystem("bzz", block_size=65536)
con = duckdb.connect()
con.register_filesystem(fs)   # the only Swarm-specific line

url = f"bzz://{root}/sales.parquet"
con.execute(f"""
    SELECT region, sum(amount)
    FROM read_parquet('{url}')
    WHERE day BETWEEN 100 AND 106
    GROUP BY region ORDER BY 2 DESC
""").fetchall()
```

Measured live against that 5.0 MB / 1 M-row file (64 KiB read blocks):

| query | transferred |
|---|---|
| `count(*)` | **1 read, 16 KB** — footer metadata only, no data pages |
| one-week filtered aggregate | **4 reads, 214 KB** — row groups pruned by the sorted `day` stats |
| full-table two-column aggregate | **41 reads, 3.9 MB** — only the `region`+`amount` columns of the 5 MB file |

That's the whole story in three rows: metadata answers what metadata
can, statistics prune what statistics can, and even a "full scan" is a
*columnar* scan. Costs scale with data touched, not file size — the
same property as swarmlite's page counting, delivered by the Parquet
layout instead of a B-tree.

## 3. Updates, history, verification

Everything from the SQLite side transfers unchanged, because it's the
same storage model:

- **Feeds** for a stable URL: write through `bzzf://<owner>/<topic>/...`
  (with a signer) instead of `bzz://new/...`; readers follow the feed,
  every previous pin remains queryable, `swarmlite snapshots` lists the
  history. Republished files share unchanged chunks.
- **Verification**: against your own light node, the node verifies every
  chunk natively. Reading through an untrusted gateway, construct the
  filesystem with swarmfs's verification enabled (see the swarmfs User
  Guide) — the verifying reader BMT-checks each chunk, and range reads
  still descend only the subtrees the query needs.
- **Stamps**: same rules — batch depth from file size, and the data is
  only paid for while the stamp lives, so treat expiry as loss (User
  Guide §3).

## 4. Choosing between the two shapes

| | SQLite + swarmlite | Parquet + DuckDB |
|---|---|---|
| point lookup by key | **~5 fetches / 20 KB** | reads whole row groups |
| text/keyword search | indexes, FTS5 | not its job |
| aggregate over millions of rows | touches every row's pages | **columnar + pruned** |
| runs in a plain browser | yes (wa-sqlite, `npm i swarmlite`) | yes (duckdb-wasm speaks httpfs/fsspec-style ranges via a gateway URL) |
| writes | publisher pattern | publisher pattern |

They compose: publish *both* views of the same dataset under one root —
`site.db` for the app's lookups and search box, `data.parquet` for the
analysts — and advance them together with one feed.
