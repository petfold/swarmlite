# Runbook: extending a published demo's postage batch

> **There is a command for this now.** `swarmlite stamps topup <batchID>
> --for 4w` does everything below — prices it, warns about the
> dilute-first trap, asks before spending, and waits for the node to apply
> it — and `swarmlite stamps --check` tells you when it is needed. Keep
> reading only if you want the raw HTTP procedure, the arithmetic behind
> it, or you are debugging the node.

Topping up buys **time**, not capacity, and it is **additive** — the
remaining TTL is kept and the purchased days are added on top. Nothing
needs republishing: the root and any `bzzf://` feed stay valid, because
the stamp is what keeps the chunks alive.

Recorded from the live run of 2026-07-29 (Bee 2.8.1) that extended the
searchable-blog demo from 24.0 to 40.1 days.

## 0. Variables

The demo hash you remember is the **manifest root**, not the batch ID —
they are different things and the topup endpoint wants the batch ID.

```bash
BEE=http://localhost:1633
ROOT=61e8818574436e412657698cb90a0db0b42d0a6ddee44b53a650125ec5172050  # searchable blog demo
```

## 1. Check the node is up

```bash
curl -s $BEE/health
# -> {"status":"ok","version":"2.8.1-...","apiVersion":"8.1.0"}
```

## 2. Find the batch ID

There is no reverse index from content to batch, so this is a judgement
call: match `depth`/`utilization` against the upload, or — if only one
batch exists — it is unambiguous.

```bash
curl -s $BEE/stamps | python3 -m json.tool
BATCH=c931c8a5ee8def225abf86a934bbf38ab191f10456f1a684c9cb44f534834359
```

Note `depth`, `batchTTL`, `immutableFlag` and `utilizationRatio` from the
output. `utilizationRatio` near 1.0 means the fullest bucket is nearly
full, so further uploads risk a `402 batch is overissued` — that blocks
*new* chunks and does not endanger what the batch already stamped. If you
plan to upload more, dilute *first* (`PATCH
/stamps/dilute/$BATCH/<depth+1>`, which doubles every bucket's capacity
and halves the remaining TTL), *then* top up — the other order pays for
time the dilution immediately halves away.

`GET /stamps/$BATCH/buckets` gives all 65 536 counters if you want the
true headroom rather than the summary ratio:

```bash
curl -s $BEE/stamps/$BATCH/buckets | python3 -c "
import json,sys
b=[x['collisions'] for x in json.load(sys.stdin)['buckets']]
print('chunks', sum(b), '| fullest bucket', max(b))"
```

## 3. Confirm the root is still alive

`/bzz/<root>/` alone 404s when the manifest has no index-document entry —
that is cosmetic, so ask for a real path.

```bash
curl -s -o /dev/null -w "HTTP %{http_code} %{size_download}B\n" $BEE/bzz/$ROOT/index.html
# -> HTTP 200 2627B
```

List everything the batch is holding up (needs the project venv, since
the `bzz` fsspec protocol comes from swarmfs):

```bash
.venv/bin/python -c "
import fsspec, sys
fs = fsspec.filesystem('bzz')
tot = 0
for e in fs.find(sys.argv[1], detail=True).values():
    print(f\"{e['size']:>10} {e['name']}\"); tot += e['size']
print('TOTAL', tot)
" $ROOT
```

## 4. Price the top-up

`addedAmount` is **per chunk** and buys `addedAmount / currentPrice * 5`
seconds (5 s Gnosis blocks). Cost in xBZZ is
`addedAmount * 2^depth / 10^16`.

```bash
curl -s $BEE/chainstate | python3 -m json.tool   # currentPrice
curl -s $BEE/wallet | python3 -m json.tool       # bzzBalance, walletAddress
```

Solve for a target — either a spend budget or a number of days. Fill in
the four values from the two calls above:

```bash
python3 - <<'EOF'
price   = 68657          # /chainstate currentPrice
depth   = 19             # /stamps[].depth
ttl_now = 2073723        # /stamps[].batchTTL, seconds
bzz     = 13207006405427200   # /wallet bzzBalance, plur (1 xBZZ = 1e16)

chunks = 2 ** depth
cost   = lambda a: a * chunks / 1e16          # xBZZ
days   = lambda a: a / price * 5 / 86400

# (a) spend a fixed budget
budget = 1.0                                  # xBZZ
a = int(budget * 1e16 // chunks)
print(f"budget {budget} xBZZ -> addedAmount={a} (+{days(a):.2f}d, "
      f"total {(ttl_now / 86400) + days(a):.2f}d)")

# (b) reach a target total lifetime
target_days = 60
a = round((target_days * 86400 - ttl_now) / 5 * price)
print(f"total {target_days}d -> addedAmount={a} (costs {cost(a):.3f} xBZZ)")

print(f"affordable ceiling: {days(int(bzz / chunks)):.1f}d for {bzz / 1e16:.4f} xBZZ")
EOF
```

Check the cost against `bzzBalance` before spending. In the recorded run
a full 2 months needed 2.24-3.73 xBZZ against a 1.32 xBZZ wallet, which
is why it became a 1 xBZZ / 16-day top-up.

## 5. Top up

Spends real xBZZ and is irreversible.

```bash
curl -s -X PATCH "$BEE/stamps/topup/$BATCH/19073486328" | python3 -m json.tool
# -> {"batchID":"c931c8a5...","txHash":"0xce216a81..."}
```

## 6. Verify

The tx returns before Bee indexes the chain event, so `amount` reads the
old value for a minute or two. Poll until it moves — do not trust the
first read.

```bash
BEFORE=32954342400   # /stamps[].amount from step 2
for i in $(seq 1 12); do
  out=$(curl -s $BEE/stamps/$BATCH)
  amt=$(echo "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["amount"])')
  ttl=$(echo "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["batchTTL"])')
  echo "try $i: amount=$amt batchTTL=$ttl ($(python3 -c "print(f'{$ttl/86400:.2f}')") days)"
  [ "$amt" != "$BEFORE" ] && { echo INDEXED; break; }
  sleep 15
done

curl -s $BEE/wallet | python3 -c 'import json,sys; print(int(json.load(sys.stdin)["bzzBalance"]) / 1e16, "xBZZ")'
```

Two checks that it worked as intended: the `amount` delta should equal
`addedAmount` exactly (proving the top-up was additive), and the wallet
should have dropped by the quoted cost.

## Caveats

- The days bought are priced at **today's** `currentPrice`. If network
  demand raises it, the batch expires sooner than the number printed here
  — re-check `batchTTL` periodically rather than trusting one calculation.
  (Measured: 68657 → 68699 within one day.)
- Top-ups stack, so a small one now plus a larger one after funding the
  wallet ends up in the same place, at the cost of extra gas.
- Batch to content is not enumerable. If a batch stamped earlier uploads
  or feed updates, they ride on the same TTL invisibly.
