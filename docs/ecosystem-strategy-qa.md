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

---

**Question (Peter):**

> To replace the web (and even non-web internet), it would be important
> to know what are the significant parts of it today. How do we even
> measure it? Big-tech (Amazon, Meta, Microsoft coulds and user facing
> services come to mind, social media in general and the big AI firms)
> come to mind as big but what would be a true measure of what counts
> as the most significant, biggest part of the internet. Could it be
> measured by bandwidth, traffic, human hours spent, use by AIs, or
> even just what people (and AI agents) know and care about. What would
> be the best parts to target? Various limitations ae coming into force
> (age/ID checks, firewalls, various restrictions) , which may make the
> Swarm Web3 ever more important.

**Answer (Claude):**

This one's worth answering carefully, because the different measures
don't just give different sizes — they describe *different internets*,
and picking the wrong one would aim the whole project at the wrong
target.

**The measures, and what each actually captures (rough shapes as of my
knowledge, which ends early 2026):**

- **By bytes/bandwidth**, the internet is a video pipe. Streaming video
  is on the order of two-thirds of all traffic (YouTube, Netflix,
  TikTok, plus adult content); add gaming downloads and OS updates and
  there's little left. The entire *textual* web — everything anyone has
  ever written — is a rounding error next to one evening of global
  Netflix.
- **By human hours**, it's feeds and chats: short video, social media,
  messaging. Attention lives almost entirely inside a half-dozen apps.
- **By money**, it's three overlapping machines: cloud B2B
  (AWS/Azure/GCP running everyone's backends), e-commerce/payments, and
  the advertising duopoly that funds the "free" layer.
- **By what is *known*** — mindshare, citations, and increasingly what
  AI models learn and retrieve — it's a completely different, much
  smaller web: Wikipedia, documentation, forums, news archives,
  scientific papers, code, maps, public records. Call it the
  **reference web**. It's negligible by bytes and modest by hours, but
  it is overwhelmingly what humans consult when they want to *know*
  something, and it's what LLM training and retrieval weight most
  heavily. TikTok contributes almost nothing to what an AI knows; Stack
  Overflow and Wikipedia contribute enormously.
- **By machine usage**, a structural shift is underway: bot and crawler
  traffic now rivals or exceeds human web requests, and AI agents are
  becoming a primary *reader* of the web — one that doesn't watch ads,
  which quietly breaks the economic model funding the human web.

**Which measure should we optimize for?** Not raw size — *value per
byte times how badly the current architecture serves it*. Video is the
biggest thing on the internet and the worst target: technically
well-served by CDNs, economically locked by licensing and DRM, and
contention-free anyway. Social feeds own attention but sit high on our
contention ladder and behind network effects. The reference web,
though, scores maximum on every axis that favors us: it's read-mostly
(our architecture, exactly), high value per byte, *small enough to
actually host* (all of Wikipedia is ~100 GB; the meaningful text web is
terabytes, not the exabytes of video), it suffers acutely from link
rot, paywalls, platform deletions and censorship — and it's the layer
whose durability matters more every year that AI mediates human
knowledge. Whoever is the substrate of record for the reference web has
leverage wildly disproportionate to its byte count.

**Your restrictions point is, I think, the strongest tailwind, and it
cuts twice.** From one side, states are enclosing access: age/ID
verification laws, national firewalls, takedown regimes — turning the
open web into a checkpoint web where reading requires identifying
yourself. From the other side, platforms are enclosing content *against
machines*: anti-crawl walls, pay-per-crawl schemes, API lockdowns. The
open web is being squeezed from both ends, and both squeezes create
constituencies for content-addressed storage: publishers who need
durable, uncensorable publication, and AI systems that need a
verifiable, legally clean data commons. Note also that Swarm's
economics fit the agentic web better than advertising does: stamps mean
content is *paid at the source* to exist, so it doesn't matter whether
the reader is a human who might see an ad or an agent that never will.
Content addresses have one more property tailor-made for the AI era: a
`bzz://` reference is a **citation that cannot rot or be silently
edited** — grounding and provenance for machine knowledge, which no URL
can honestly promise.

**So the target list, in order:**

1. **The reference web as archive**: Wikipedia and documentation
   mirrors, scientific papers and datasets, news archives, public
   records, OpenStreetMap data. All read-mostly, all high-mindshare,
   all publishable with what we've already built. The slogan writes
   itself: *the web that AI reads, made permanent and verifiable.*
2. **The independent long tail**: blogs and small sites (the
   WordPress-exporter path) — they die of hosting costs and bit rot,
   and permanence-for-a-stamp is a real pitch.
3. **Data for agents**: structured datasets with verifiable
   provenance — swarmlite/recordstore as the retrieval substrate,
   content addresses as citations.
4. Then up the contention ladder as before: social (a person's posts
   are *literally* a feed — our primitive; the hard part is aggregation
   and discovery), and commerce/booking at the contract-arbitrated top.

And to your "how do we even measure it" — I'd propose the project's
north-star metric be neither bytes nor hours but something like
**verifiable citation share**: of the things people and AIs actually
consult and cite, what fraction could resolve to a content address
instead of a rotting, gateable URL. That's the fraction of the internet
that we can honestly claim to have replaced.

---

**Question (Peter):**

> You may have seen the apps available on Swarm Desktop such as
> http://localhost:1633/bzz/bc9b942212421e2a19fe1ffdf0add641ae530923041ea8f549381747b14b2f2d/
> and
> http://localhost:1633/bzz/ab77201f6541a9ceafb98a46c643273cfa397a87798273dd17feb2aa366ce2e6/ ,
> the latter also accessible from Freedom Browser https://freedom.baby/
> as bzz://freemap.eth/ , as well as app.swarmit.eth/  so we are making
> progress in that direction

**Answer (Claude):**

Both identified, and they slot exactly into the target list from the
last discussion:

- `bc9b9422…` is a **Swarm Decentralised Wiki** — that's literally item
  #1 of the reference-web strategy (Wikipedia-class knowledge, made
  permanent).
- `ab77201f…` / `bzz://freemap.eth` is **Leaflet + map tiles served
  from Swarm** — the maps entry, one rung below the route-planning demo
  on the same ladder (tiles are pre-rendered answers; a queryable graph
  is the upgrade).

*(Live check during the session: the local Bee resolves
`/bzz/freemap.eth/` → HTTP 200 — Swarm Desktop ships with an ENS
resolver configured.)*

Good news on the naming front too: your local Bee already resolves
ENS — `http://localhost:1633/bzz/freemap.eth/` returns FreeMap
directly, so Swarm Desktop ships with a resolver configured. That's
genuine progress on exactly the axes we discussed: the wiki is
reference-web content, FreeMap is reference-web data, Freedom Browser
is native `bzz://` access with human-readable names, and swarmit is
user publishing. The tier-1 layer is visibly assembling.

It also exposes two concrete, cheap opportunities for our stack that
I'd flag for the queue:

1. **ENS pass-through in swarmfs/swarmlite.** The error you hit days
   ago literally says "ENS names are not supported yet" — but it turns
   out the node happily resolves them server-side. So the fix isn't
   implementing ENS at all; it's swarmfs *not rejecting* non-hex names
   and letting Bee resolve `bzz://freemap.eth/...` on reads. Small
   change, and suddenly `swarmlite query "bzz://mysite.eth/site.db"
   ...` works, which is the URL shape humans will actually share.
   (Client-side *verification* against a name is a different matter — a
   name must first resolve to a root you trust, which needs an eth RPC
   or the gateway's word — worth documenting as the same trust split we
   already have.)

2. **Both apps are missing exactly the piece we build.** A static wiki
   has pages but no *search* — pre-rendered answers, combinatorial
   question space, the precise gap from our static-vs-database
   discussion. A swarmlite index over the wiki's articles (title +
   FTS-style keyword table, published beside it under the same root)
   would give it a working search box with no server, using the npm
   reader we just packaged. Same story for FreeMap: it has tiles but no
   *geocoding* — type "Balatonfüred", get coordinates, fly there.
   That's a places table with a point lookup: our 5-pages-per-query
   bread and butter. Either one would be a flagship integration with an
   app that already ships in Swarm Desktop, and both are
   days-not-weeks of work on top of what exists.

So the ecosystem is converging on the same architecture from two
sides: they're publishing the content and the viewers; we're supplying
the queryable-data layer those viewers will inevitably need.

---

**Question (Peter):**

> We started swarmlite to support existing SQL infrastructure and
> developer skills. SQL has a long and deep theory and highly developed
> infrastructure. But relational DBMSs are not a perfect fit for Swarm.
> Maybe functional databases are better (something to consider: would
> Swarm want to substitute them, or pretend to be them). But an
> alternative is OntoDAG, which is now for ontologies but I am
> wondering whether it could be made into a proper database. And if so,
> what is missing from OntoDAG at the moment.

**Answer (Claude):** *(after reading OntoDAG's HOW_IT_WORKS, roadmap,
and the recordstore lineage)*

**1. The relational misfit, stated precisely.** SQL's misfit with
Swarm isn't the relational *model* — it's that mainstream RDBMSs are
write-oriented server processes: their pages, locks, and transaction
machinery all assume a single mutable store with an owner. swarmlite
sidesteps this by amputation (read-only snapshots), which is honest but
concedes the point: we're using 10% of what a DBMS is.

"Functional database" — in the Datomic sense: the database is an
immutable value; writes accumulate facts rather than overwrite; queries
run against a point-in-time value; time travel is free — is not just a
better fit for Swarm, it's almost a description of it. "The database as
a value" becomes literal: the value *is* the root hash. Datomic
simulates immutability on mutable storage; Swarm provides it natively.
On substitute-vs-pretend: it's a ladder, and we're already on it —
*pretend* (swarmlite's SQL facade) is the adoption play; *substitute*
(a native immutable store) is the architecture play; and the two
connect through materialization (the native store publishes SQL views
as site.db for the facade's readers). recordstore already sits
underneath as the shared value layer.

**2. What OntoDAG already has — more database than it admits:** a
canonical form with a unique fingerprint (same knowledge ⇒ same root:
*semantic* content addressing, deduplication of meaning — no SQL or
Datomic database has this); a real query planner with exact statistics
and provably result-preserving rewrites; CRDT merge (commutative,
idempotent) — multi-writer convergence *without consensus*, the
property no pretend-SQL layer can ever offer; and versioned,
structurally-shared persistence on content-addressed storage.

**3. What's missing, in priority order:** (1) *values* — typed
attributes and predicates over them; two mechanisms: attribute payloads
with ordered secondary indexes, and the OntoDAG-native *dimension
lattices* (calendar chains, geo quad-trees) that make range queries
just cones; (2) *relations beyond is-a* — "Alice authored Doc1" is not
subsumption; the cleanest path is a datom layer (entity, relation,
value) beside the DAG, with the DAG supplying what EAV systems lack: a
principled taxonomy of entities and relations; (3) *query
completeness* — union (trivial), negation (needs a closed-world
decision), projection, eventually rules; (4) *a lazy remote reader* —
the swarmlite lesson: stop loading everything into RAM; recordstore
already supports lazy fetches; cached cone summaries make an OntoDAG
queryable from a browser at tens of fetches per query; (5) *delete
semantics under merge* (tombstones or a declared grow-only stance);
(6) *constraints* — disjointness at minimum.

**4. Strategically**, OntoDAG-as-database wouldn't compete with
swarmlite — it sits above it as the native, multi-writer, semantically
canonical layer, publishing materialized SQL views downward. The unique
selling point: **the only database where identical knowledge has an
identical address, and independent writers converge without a server.**

---

**Follow-up (Peter):**

> About the additional features you suggest: they are interesting but
> my strategy was to keep it as pure and simple as possible to see how
> far we can go with the simplest approach. Only add non-ontodag pure
> features when we hit a wall. An example is geo information: don't
> integrate a geo engine is we can do it in the spirit of ontodag.

**Answer (Claude):**

The purity strategy is methodologically right, and not for aesthetic
reasons: OntoDAG's two invariants (canonical form, mergeability) are
load-bearing, and nearly every "database feature" on my list is a
potential threat to one of them. Arbitrary secondary indexes threaten
"same knowledge = same root" unless derived deterministically;
tombstones complicate idempotent merge; relations imported wholesale
would dilute the one-primitive query model that makes the planner
provable. Purity here is what the guarantees are made of.

Re-sorted under the principle:

*Pure now (no model change):* the **lazy remote reader** (pure
engineering, zero semantics — and it's what makes OntoDAG demonstrable
on Swarm at scale); **dimension lattices** for time/geo/numeric ranges
— values-as-categories IS the ontodag-spirit geo engine ("photos of
dogs from last summer" = one cone intersection; the lattice generator
is tooling, not model); **union** in get() (query-side set union,
canonical form untouched).

*Walls, with tripwires* (documented so a future session recognizes
hitting one, rather than pre-building): exact arithmetic/equality on
continuous values (lattices quantize; tripwire: a query that needs
computation, not classification); arbitrary relations (tripwire: users
mass-reifying relation nodes like "authored-by-Alice"); aggregation
beyond the free COUNT (descendant_count); negation (requires a
closed-world decision); deletion under multi-writer merge (tripwire:
first real collaborative deployment); disjointness constraints
(tripwire: garbage queries that a Cat∧Dog check would have refused).

This analysis is saved in the OntoDAG repo as
`docs/DATABASE_DIRECTION.md`, written to stand alone as the kickoff
context for a future session working there.

---

**Question (Peter):**

> In the searchable blog demo, what are the options for being able to
> comment. And how long in different solutions would it take for the
> comment to appear? ... Or possibly wait for the GSOC / pub-sub
> solution that is being promised soon.

**Answer (Claude):**

Comments are the "single-writer interactivity" tier from the
contention ladder above: many potential writers, but the blogger
ultimately owns and serializes their own comment section — no stranger
contention, so no blockchain needed, just *some* channel from a
reader's browser to a read-only published snapshot. Options considered,
each with honest latency:

1. **Out-of-band submission, manual/scripted republish** — a form or
   `mailto:` collects comments elsewhere; the blogger periodically
   folds them into `comments` rows and republishes with `swarmlite
   publish --feed`. Appears whenever that publish step next runs —
   minutes if automated on submit, more realistically hours to days
   batched. Zero new infrastructure; the pattern is already in
   DESIGN.md §5.
2. **Reader writes their own signed feed, blog page aggregates known
   feeds client-side.** Appears as fast as feed propagation — measured
   live elsewhere in this project at ~2–3 minutes on Gnosis mainnet.
   No blogger action needed, but *discovery* of which feeds to check
   requires a registry the blogger publishes (hand-rolled pub/sub).
3. **A lightweight centralized relay for intake.** Minutes, nicer UX,
   but reintroduces exactly the trusted server the architecture exists
   to avoid.
4. **True multi-writer via recordstore `commit(reconcile=True)`** over
   a shared feed — best-effort, not race-free by the library's own
   admission. Raw comment exists within feed-propagation minutes, but
   nobody has built a *live-merge reader* that shows it on the page
   without a full republish.

**Decision: wait for GSOC/pub-sub, do not build any of the above now.**
GSOC (Graffiti/Generalized Single-Owner Chunks) plus pub/sub is a
different shape than all four options: push instead of poll, no relay,
no discovery registry to hand-roll — swarm-kit's `provider.ts` already
exposes `sendGsoc`/`subscribe` primitives aimed at exactly this
many-writers-broadcast-to-many-readers pattern (seen directly while
preparing the swarm-kit integration PR). If it lands as designed,
comment latency drops to live-chat speed, genuinely serverless,
leapfrogging every hand-rolled option above — including recordstore
multi-writer, since GSOC is purpose-built for broadcast rather than
repurposing single-owner sequential feeds.

**Implication for later sessions:** don't invest in option 2's
feed-registry plumbing in the meantime — it solves the same discovery
problem GSOC/pub-sub will solve properly, and that engineering would
likely be thrown away at that point. The WordPress demo stays
comment-less until GSOC/pub-sub is available (or demand becomes urgent
enough to accept option 1 as a deliberate stopgap).
