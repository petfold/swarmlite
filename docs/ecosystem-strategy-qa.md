# Ecosystem strategy Q&A (2026-07-24)

A verbatim exchange from a working session, kept as a planning
reference for the post-v2.1 direction.

---

**Question (Peter):**

> A broader question: our high-level goal is to be the "new internet",
> the real web3. So one thought was to let developers who are familiar
> with the currently populat development stack (e.g. LAMP) to easily
> move to Swarm, and the missing piece seemed to be the SQL database.
> Now we have one, but it's in Python. As far as I know most web
> development is not in Python. So what can we do about that, the
> non-python (large) part. Even within Python, maybe SQLite is not the
> most used SQL database. And what about things like a MongoDB
> interface?

**Answer (Claude):**

Big question, and worth answering honestly rather than cheerfully. Let
me take it apart into the four claims hiding inside it.

**1. "It's in Python" is less true than it looks — the strategic half
is already JavaScript.**

In the architecture we've built, there are two roles, and they have
different language needs:

- **The read path** — where web apps actually *run* — is the browser,
  and that's done: `js/` is a complete JS reader (wa-sqlite VFS, feeds,
  client-side verification), which also runs unmodified in Node (our
  smoke tests are Node). In the serverless model, the client *is* the
  application runtime; there is no server tier where PHP or Java would
  live. So for the audience that matters most, the language question is
  already answered — it's JS, and we have JS.
- **The publish path** is Python today, but publishing is an *offline,
  local* act — closer to `rsync` or a static-site generator than to an
  app framework. Any stack can shell out to the `swarmlite` CLI
  already. Python here is tooling, not platform.

The real gap in the JS story isn't capability, it's **packaging**: the
reader lives in a repo subdirectory instead of being `npm install
@swarmlite/reader`, and there's no pure-JS publisher, so a full-stack
JS developer still needs Python for the write side. Both are very
tractable: the publisher is "pragma checklist + upload + feed", the
checklist can run on wa-sqlite in Node, and bee-js covers transport.
That's the single highest-leverage move for reach.

**2. "SQLite is not the most-used SQL database" — right fact, wrong
comparison.**

MySQL and Postgres are *server processes*. Their defining value — many
concurrent writers, live transactions over the wire — is precisely what
a content-addressed storage network cannot and should not offer. You
can't lazy-page a running Postgres over immutable chunks; there's
nothing there to publish. SQLite is the only mainstream SQL engine that
is a *file format plus an embeddable library*, which is exactly the
shape a serverless web needs — and by deployment count it's the most
used database on Earth (every browser and phone ships it), so the
reader-side bet is safe.

The story for MySQL/Postgres developers therefore isn't "switch
databases" — it's "**keep your operational database; publish a SQLite
snapshot as your public read replica**." That's a one-step ETL
(`pg_dump`→sqlite, or a ten-line script), and it slots into the
publisher pattern we already have: cron materializes `site.db` from
Postgres, `swarmlite publish --feed` ships it. What's missing is a
*cookbook* documenting that recipe, not code.

**3. The honest LAMP caveat: the hard part was never SQL syntax.**

Mapping LAMP → Swarm: Linux→the network, Apache→gateway static serving,
MySQL→swarmlite. But the **P** doesn't map — PHP's mental model is "the
server executes my code on every request and writes to the DB on POST."
That model *cannot* transfer to any decentralized storage, ours or
anyone's. What we offer is the read-mostly web (blogs, catalogs, docs,
search, dashboards — realistically 95% of pages served), with writes
flowing through the publisher pattern. Migration tooling should
therefore target artifacts, not frameworks: a **WordPress exporter**
(posts into `site.db` + a static theme + FTS search box) would be the
literal "move your LAMP site to Swarm" demo, and far more persuasive to
that audience than any library.

**4. MongoDB: the primitive already exists in the family — a facade
can wait.**

Two answers here. First, document-style querying *inside* swarmlite
already works: SQLite's JSON1 is how we built the demo's keyword index
(`json_each`), so "store JSON docs, query by field" is a
publishable-today pattern. Second, **recordstore is the document
store** — JSON records under keys, versioned, canonical roots — and a
Mongo-flavored query API (`find({author: ...})` with materialized
secondary indexes) would be a thin layer over it. What I'd *not* chase
is MongoDB wire-protocol compatibility: like MySQL, that promises a
live multi-writer server, which is a category error on immutable
storage. Demand should pull that facade into existence, not roadmap
push.

**So, concretely, in priority order:** (1) package `@swarmlite/reader`
on npm; (2) a pure-JS publisher so JS devs never touch Python; (3) the
"publish your Postgres/MySQL as a read replica" cookbook; (4) the
WordPress-export demo as the migration flagship; (5) DuckDB/Parquet
cookbook for the analytics crowd (already on the roadmap); (6) Go/Rust
readers only when server-side consumers (indexers, bots) actually show
up.

Want me to start on (1) and (2)? Those two together turn "a Python
project with a JS demo" into "a JS-first product with Python tooling,"
which matches where web developers actually are.

---

**Question (Peter):**

> I am not familiar with current website backends, so it's interesting
> that they use databases for the "read-mostly web (blogs, catalogs,
> docs, search, dashboards". To me it sounds like those could be static
> pages but then I don't know much about web servers. What I tought
> would need more real database functionality are things like booking
> sites or route planning, where the publishing and the reading seem to
> me to be less separated. What would our strategy be for those use
> cases. It is probably best to follow your plan first, I am just
> curious to look ahead as to where the longer term plan leads.

**Answer (Claude):**

Great instinct on both counts — you've spotted the real seam. Let me
take the two halves separately.

**Why the read-mostly web uses databases at all.** A static page is a
*materialized answer to one pre-decided question*. That works when the
question space is small and known in advance: each blog post is one
page, so generators like Hugo happily pre-render them all. It breaks
when the question space is combinatorial: search ("posts containing
*verifiable*"), catalog filtering (size × color × price × brand),
sorting, pagination over arbitrary filters. You cannot pre-render every
possible search result — so the LAMP answer was to keep a server
running MySQL and compute each answer on demand. WordPress renders even
a plain blog post from the database for workflow reasons (authoring,
templates, tags), but the *structural* reason databases sit behind
catalogs and search is that readers choose the question at read time.
That's exactly the gap swarmlite fills: it makes the *question space*
static-hostable. The database is one immutable artifact; the reader
runs whatever `SELECT` they want, lazily. Our demo is precisely that
proof — a search box over 42 MB with no server.

**Route planning: surprisingly, it's already ours.** It feels
interactive, but decompose it: the road network is read-mostly data (a
publisher updates it daily); the *computation* — shortest path — is
per-reader and touches a tiny fraction of the graph. That's the
swarmlite shape exactly. Real routing engines precompute index
structures (contraction hierarchies) that make queries touch few,
predictable nodes — a preprocessed graph in SQLite with lazy page reads
means client-side routing over a static published file. Reading and
publishing only *seem* less separated; actually the write path (map
updates) and read path (your route query) are perfectly separated. This
would make a spectacular flagship demo someday — "route across a
country against a static file on Swarm."

**Booking is the genuinely hard one, and it's worth being precise
about why.** The essence of booking is *contention over a scarce
resource*: two strangers want the last room, and someone must decide
who was first. That is not a storage problem — it's an
ordering/consensus problem, and no storage cleverness dissolves it. The
LAMP web solved it with a MySQL row lock, which worked because one
company owned the database; the lock *was* the trusted arbiter.
Decentralization changes who you must trust, not the physics of
contention. Anyone promising serverless booking without a consensus
layer has hidden a server somewhere.

So the long-term strategy is a ladder, by how much contention the use
case actually has:

1. **Reader-computes** (search, routing, analytics, maps, dashboards) —
   no contention at all; already covered by what we've built. Much more
   of the "interactive" web lives here than it first appears.
2. **Single-writer interactivity** (orders to one shop, bookings with
   *one* hotel, comments on a moderated blog) — the resource owner is
   inherently a trusted party for their own inventory, so let them be
   the serializer: intents arrive at a small intake endpoint (or via
   chain messages), the owner's publisher applies them locally and
   advances the feed. The feed becomes a signed, append-only booking
   ledger — `swarmlite snapshots` is literally the audit trail.
   DESIGN.md §5 already sketches this; what's missing is a cookbook and
   maybe a tiny reference "intake relay."
3. **True multi-party contention** — this is where the blockchain earns
   its place in the stack, and only here: a smart contract arbitrates
   the scarce slot (first valid transaction wins; ~100 bytes of
   consensus), while Swarm carries everything heavy — the catalog,
   descriptions, images, availability projections (megabytes). An
   indexer watches chain events, materializes them plus the catalog
   into `site.db`, and republishes — closing the loop back into our
   read path. That split — *chain for conflict decisions, Swarm for
   data* — is arguably what "the real web3" architecture means; the
   chain is the write path of last resort, and 99.99% of the bytes stay
   with us.

The near-term plan stands unchanged — npm publish, cookbooks, migration
demos. But when we look up from that, the natural next artifacts are:
the route-planning demo (flagship for tier 1), the "single-writer shop"
cookbook with feed-as-ledger (tier 2), and eventually a
contract-plus-indexer reference for contested booking (tier 3).
