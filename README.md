# swarmlite

**Verifiable serverless SQLite hosting on Ethereum Swarm.**

Publish an ordinary SQLite database file to [Swarm](https://docs.ethswarm.org/)
and run `SELECT` against it — from Python or (later) the browser — fetching
only the B-tree pages the query plan touches, each page verifiable against
the file's content address. No database server anywhere.

**Status: pre-alpha scaffold.** The design is settled (see
[docs/DESIGN.md](docs/DESIGN.md)); implementation is starting. Nothing below
works yet unless marked otherwise.

## The idea in one paragraph

SQLite never touches storage directly — all I/O goes through its **VFS**
interface, and a *read-only* VFS needs just three methods (`xOpen`, `xRead`,
`xFileSize`). SQLite's default page size is 4 KB; so is a Swarm chunk. So a
thin VFS that maps `xRead(offset, n)` onto ranged reads of a `bzz://`
reference (which [swarmfs](https://github.com/petfold/swarmfs) already
provides) gives you lazy `SELECT` over a multi-gigabyte remote database at a
cost of a handful of 4 KB page fetches per query — Merkle-verified, immutable,
permanent. Precedent: phiresky's `sql.js-httpvfs` did this over plain HTTP
range requests; Swarm adds verification and permanence.

Writes stay where SQL writes belong: the **publisher** works on a local file
with full SQL and real transactions, then ships an immutable snapshot and
advances a feed. Never a SQL `UPDATE` across the network.

## Quick start (once the prototype exists)

```bash
# until swarmfs is on PyPI, install it from a local checkout first:
pip install -e ../swarmfs -e ".[test]"
pytest                       # offline unit tests (no Bee node needed)
```

Read path:

```python
import swarmlite

# immutable pin: a specific published version
con = swarmlite.connect("bzz://<64-hex-reference>/site.db")

# or follow a feed: always the latest published version
con = swarmlite.connect("bzzf://<owner>/site/root")

for row in con.execute(
        "SELECT title FROM posts ORDER BY ts DESC LIMIT 10"):
    print(row)
```

Publish path:

```bash
swarmlite publish site.db --feed site/root
# VACUUM -> upload via swarmfs transaction -> feed update
```

## Architecture

```
application          plain SELECT
SQLite engine        unmodified (via apsw)
bzz-VFS shim         xRead(off, n) -> ranged fetch     <- this package
swarmfs              f.seek(off); f.read(n)            exists today
Bee                  /bytes, HTTP range reads
```

See [docs/DESIGN.md](docs/DESIGN.md) for the full design (read path, page
cache, publisher checklist, browser/WASM phase, web-app patterns, caveats)
and [docs/roadmap.md](docs/roadmap.md) for the build order.

## What this is not

- Not a SQL engine over Swarm-native indexes — that is
  [recordstore](https://github.com/petfold/recordstore)/POT territory.
- Not OLTP. Feeds are eventually consistent and last-write-wins; readers
  wanting reproducibility pin a root instead of following the feed.
- Not a write path through the VFS. Read-only by design; the publisher
  pattern *is* the write API.

## Relatives

- [swarmfs](https://github.com/petfold/swarmfs) — the fsspec backend this
  builds on (range reads, transactions, feeds).
- [recordstore](https://github.com/petfold/recordstore) — versioned
  key→record store; can be the system of record that `site.db` is
  materialized *from* (the datacat pattern).
- DuckDB-WASM + Parquet over range requests — the analytics flavour of the
  same trick; works today via swarmfs with no new code.

## License

BSD 3-Clause. Author: Peter Foldiak.
