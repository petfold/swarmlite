# Cookbook: publish your Postgres/MySQL as a Swarm read replica

You do not have to leave your operational database to put your data on
the verifiable web. Keep Postgres/MySQL exactly as it is; publish a
**SQLite snapshot of the reader-facing views** to Swarm on a schedule.
Readers get serverless, verifiable SQL; your write path doesn't change
by one line.

Everything below was run for real (an operational 50 k-row table →
3.0 MB replica → published → queried at **10 pages / 40 KB** per
category search).

## 1. Decide what readers get — views, not dumps

A replica is a *publication*, not a backup: everything in it is public
and permanent while stamped. Choose reader-facing SELECTs (drop internal
columns, pre-join if it helps), and add the indexes **for the queries
readers will run** — on Swarm an unindexed scan downloads the whole
file, so publisher-side indexing is the whole game.

## 2. The materializer (any DB-API source)

One function covers Postgres (`psycopg2`), MySQL (`pymysql`), even
another SQLite — it only touches the cursor API:

```python
import sqlite3

def materialize(src, out_path, views):
    """Copy reader-facing SELECTs from any DB-API source into a fresh
    SQLite file. views = {table: (select_sql, [index_sql, ...])}."""
    con = sqlite3.connect(out_path)
    for table, (select_sql, indexes) in views.items():
        cur = src.cursor()
        cur.execute(select_sql)
        cols = [d[0] for d in cur.description]
        con.execute(f'CREATE TABLE "{table}"({",".join(cols)})')
        ph = ",".join("?" * len(cols))
        while rows := cur.fetchmany(10_000):
            con.executemany(f'INSERT INTO "{table}" VALUES({ph})', rows)
        for ix in indexes:
            con.execute(ix)
    con.commit()
    con.close()
```

Wire it to your source and views:

```python
import psycopg2  # or pymysql, or sqlite3 — anything DB-API
src = psycopg2.connect("postgresql://readonly@db.internal/shop")

materialize(src, "replica.db", {
    "products": (
        "SELECT id, name, category, price FROM products WHERE stock > 0",
        ["CREATE INDEX products_category ON products(category, price)"],
    ),
})
```

Notes:
- **Column affinity:** the `CREATE TABLE` above declares no types, which
  SQLite happily accepts (values keep their Python types). Declare types
  explicitly if readers rely on affinity or you want `INTEGER PRIMARY
  KEY` rowid optimization for point lookups on `id`.
- **Big tables:** `fetchmany` streams; memory stays flat. For 100 GB
  tables consider a `WHERE`-sharded loop, or Postgres `\copy ... TO
  CSV` piped into `sqlite3 replica.db ".import ..."` — same result.
- Don't bother with `page_size`/`ANALYZE`/`VACUUM` here — the publisher
  checklist does all of it on a copy.

## 3. Publish — into a feed, from the first version

```bash
swarmlite publish replica.db --feed products --signer "$SWARMLITE_SIGNER"
# pin:  bzz://<root>/replica.db          <- this exact version, forever
# feed: bzzf://<owner>/products/replica.db   <- always the latest
```

The feed is the point: your cron republishes, the `bzzf://` URL readers
hold never changes, and every previous pin stays valid —
`swarmlite snapshots "bzzf://<owner>/products/replica.db"` lists the
whole version history (a free audit trail). Consecutive replicas share
most chunks, so a nightly publish of a slowly-changing dataset costs
far less storage than size × days.

Cron the pair:

```cron
15 4 * * *  cd /srv/replica && python materialize.py && \
            swarmlite publish replica.db --feed products --quiet
```

Stamps: `--buy --ttl 4w --yes` self-serves a batch when none is usable
(see User Guide §3 for sizing — replicas over 15 MB want depth 19), and
**the data dies with its stamp** — monitor `batchTTL` or top up.

## 4. What readers do

```bash
pip install swarmlite
swarmlite query "bzzf://<owner>/products/replica.db" \
    "SELECT name, price FROM products WHERE category = 'cat-7' ORDER BY price LIMIT 3" --stats
# Product 287    4.29 ...
# fetched 10 pages (40 KB) in 10 reads, of a 3.0 MB file
```

Same from Python (`swarmlite.connect`), and from any browser with the
npm package — `open(url)` against a gateway, `{verify: true}` if the
gateway isn't yours. No server of yours is involved in any read, and a
reader who pins a root can reproduce results forever.

## 5. When this pattern fits

Read-mostly consumption of operational data: product catalogs, open
data, dashboards, price lists, documentation indexes. The staleness
window is your cron interval — readers see snapshots, not live rows.
What it is *not*: a replication protocol (no WAL shipping) and not for
data you may need to retract — published bytes can expire, but anyone
may have pinned them.
