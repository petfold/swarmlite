# swarmlite Reference

Compact, definition-first, no narrative. The tutorial lives in the
[User Guide](USER_GUIDE.md); design in [DESIGN.md](DESIGN.md); operational
recipes in the cookbooks and the [stamp runbook](runbook-stamp-topup.md).
The one dependency keeps its own test-pinned reference:
[swarmfs](https://github.com/petfold/swarmfs/blob/main/docs/REFERENCE.md)
— **all** storage/transport goes through it, reached via fsspec's protocol
registry (`bzz://`/`bzzf://` URLs), not direct imports.
([recordstore](https://github.com/petfold/recordstore/blob/main/docs/REFERENCE.md)
is a relative, not a dependency.)
Tables here are pinned against the code by `tests/test_reference.py` — if a
name or parameter in this file and the code disagree, the suite fails.

Package version this file describes: `0.3.0`.

## 1. Vocabulary

| term | definition |
|---|---|
| pin | An immutable published version: `bzz://<root>/<name>` — reproducible forever (while stamped). |
| feed | A stable, updatable URL: `bzzf://<owner>/<topic>/<name>`, owner-signed; every republish advances it and old pins keep working. |
| stamp / batch | Prepaid storage rights; publishing needs one, reading never does. Expiry takes the content with it. |
| page | SQLite's 4096-byte unit; the VFS fetches and caches pages. |
| block | The transport's readahead unit (`block_size`); one VFS cache miss pulls one block. |
| prepare | The publisher's checklist run on a copy: `journal_mode=DELETE`, `page_size=4096`, `ANALYZE`, `VACUUM`, `integrity_check`. |

## 2. Install

| command | gives |
|---|---|
| `pip install swarmlite` | reader + publisher + CLI (pulls swarmfs ≥ 0.7.1, apsw, fsspec) |
| `pip install "swarmlite[feeds]"` | feed publishing/verification (swarmfs's feeds extra) |

## 3. Exports

Everything importable from `swarmlite` (exactly `__all__`, minus
`__version__`):

| name | one line |
|---|---|
| `connect` | open a published database read-only → an apsw `Connection` |
| `publish` | prepare a copy and upload it: pin, optionally + feed update |
| `snapshots` | every version a feed has published, oldest first |
| `Snapshot` | one feed entry: `index`, `root`, `timestamp` (root/timestamp None when unretrievable) |
| `SwarmVFS` | the registered apsw VFS class (plumbing; `connect` is the API) |

## 4. Reading

| member | signature | semantics |
|---|---|---|
| `connect` | `(url, *, cache_pages=1024, **storage_options)` | apsw `Connection`, strictly read-only (DML/DDL raises `apsw.ReadOnlyError`). `cache_pages` sizes the 4 KB-page LRU (default ≈ 4 MB). Other kwargs go to the fsspec filesystem (`api_url=`, `block_size=`, …). `con.swarmlite_file.stats()` → the counters below. |

URL forms accepted by `connect` (and `swarmlite query`):

| form | meaning |
|---|---|
| `bzz://<root>/<name>` | immutable pin (128-hex `<root>` = encrypted; the node decrypts for whoever holds the full reference) |
| `bzzf://<owner>/<topic>/<name>` | feed, resolved to the latest version at open time (fresh feeds take minutes to become resolvable) |
| `file://…`, `memory://…` | local/testing, same code path |

`stats()` fields: `url`, `file_size`, `read_count`, `pages_fetched`,
`bytes_fetched`, `cached_pages` — what the **VFS** requested; the network
may transfer more (whole blocks).

Tuning: point lookups want small blocks (the 64 KiB default); scans want
large ones (`block_size=2**20`). Two caches cooperate — swarmlite's page
LRU and the transport's readahead — so measure with `stats()` (pages) and
node traffic (blocks) when it matters.

## 5. Publishing

| member | signature | semantics |
|---|---|---|
| `publish` | `(db_path, *, name=None, feed=None, stamp="auto", signer=None, api_url=None, encrypt=False, quiet=False)` | run *prepare* on a temporary copy (the source is never touched; live WAL databases are checkpointed via the backup API), upload inside a swarmfs transaction, return the root. `feed=` also publishes a signed feed update (signer hex or `$SWARMLITE_SIGNER`; needs `[feeds]`). `encrypt=` encrypts everything node-side (swarmfs ≥ 0.9): the root becomes 128-hex — **the URL is the secret**; readers need no flag, feeds carry the full reference, and `--buy` sizing accounts for the larger encrypted chunk count. Fixable issues are warnings; a failed `integrity_check` aborts. |
| `snapshots` | `(url, *, verify=False, **storage_options)` | list a feed's history as `Snapshot`s; `verify=True` signature-checks every update (needs `[feeds]`). |

(`prepare(db_path, out_path)` — the checklist alone, returning the warning
list — lives in `swarmlite.publish`'s module and is importable as
`from swarmlite.publish import prepare`; the module is shadowed by the
function at package level, hence no dotted row.)

## 6. Stamps (`swarmlite.stamps`)

The CLI-backing verbs. Plans are pure questions; only the verbs spend
xBZZ from the node's wallet. `StampInfo`, `BatchPlan`, `BucketStats`,
`TopupPlan`, `DilutePlan`, `suggest_depth`, `stamped_chunks`,
`depth_for_addresses` are re-exported from
[`swarmfs.stamps`](https://github.com/petfold/swarmfs/blob/main/docs/REFERENCE.md).

| member | signature | semantics |
|---|---|---|
| `stamps.plan_batch` | `(size_bytes, ttl_secs, api_url=None, **sizing)` | size a batch for a payload at the node's current price; `depth=` overrides (pair with `depth_for_addresses` for exact sizing). |
| `stamps.buy_batch` | `(api_url, amount, depth)` | buy it (waits ~40 s for on-chain confirmation + node indexing). |
| `stamps.list_batches` | `(api_url=None)` | the node's batches as `StampInfo`s. |
| `stamps.batch_buckets` | `(api_url, batch_id)` | true bucket occupancy (`BucketStats`) — measured, not estimated. |
| `stamps.plan_topup` | `(api_url, batch_id, **target)` | extend life BY `ttl_secs=`, TO `total_ttl_secs=`, or for at most `budget_bzz=`. |
| `stamps.topup_batch` | `(api_url, batch_id, added_amount)` | apply a topup (additive; polls until the node shows it). |
| `stamps.plan_dilute` | `(api_url, batch_id, to_depth)` | capacity plan: each depth step doubles buckets, halves remaining TTL. |
| `stamps.dilute_batch` | `(api_url, batch_id, to_depth)` | apply a dilution. |
| `stamps.parse_ttl` | `(text)` | `"36h"`/`"7d"`/`"4w"` → seconds. |

Depth for swarmlite's upload shape (erasure level 2, unencrypted, at
`suggest_depth`'s default 1% overflow risk — what `--buy` computes;
moved here from the User Guide):

| upload size | depth | theoretical capacity |
|-------------|-------|----------------------|
| ≤ 2 MB      | 17    | 512 MB               |
| ≤ 24 MB     | 18    | 1 GB                 |
| ≤ 166 MB    | 19    | 2 GB                 |
| ≤ 731 MB    | 20    | 4 GB                 |
| ≤ 2.4 GB    | 21    | 8 GB                 |

Lifetime arithmetic: validity ≈ `amount / currentPrice × 5` seconds
(Gnosis block time; `currentPrice` from `GET /chainstate`); cost in xBZZ
= `amount × 2^depth / 10^16`.

## 7. CLI

`swarmlite COMMAND …` (`--api-url` everywhere; else `$BEE_API_URL`, else
localhost:1633):

| command | key flags | does |
|---|---|---|
| `publish DB` | `--name`, `--feed TOPIC`, `--signer HEX` (or `$SWARMLITE_SIGNER`), `--stamp ID`, `--buy`, `--ttl 36h/7d/4w`, `--encrypt`, `--yes`, `--quiet` | prepare + upload; prints `pin:` and (with a feed) `feed:` URLs. `--buy` sizes, quotes the exact xBZZ cost, and asks first. |
| `query URL SQL` | `--stats`, `--block-size N` | run SQL against a published database; `--stats` prints the page-economy line. |
| `stamps [list]` | | batches with usability, TTL, fullest-bucket occupancy. |
| `stamps check ID` | `--min-ttl` | exit non-zero when renewal is needed (cron-friendly). |
| `stamps topup ID` | `--ttl` / `--to-ttl` / `--budget`, `--yes` | extend a batch's life (spends xBZZ). |
| `stamps dilute ID` | `--depth`, `--yes` | raise capacity (halves remaining TTL per step). |
| `snapshots URL` | `--verify` | every version a feed has published, with timestamps and roots. |

## 8. Errors

| raised | when |
|---|---|
| `apsw.ReadOnlyError` | any write attempt through a connection — publishing is the only write path. |
| `swarmfs.StampError` | no usable postage batch at publish/buy time (validated before any byte moves). |
| `swarmfs.feeds.FeedError` | malformed/misowned feed data; `snapshots(verify=True)` failures; missing signer. |
| `FileNotFoundError` | unresolvable URL / reference (fsspec semantics). |
| `ValueError` | bad TTL text, malformed feed URL, missing required arguments. |
