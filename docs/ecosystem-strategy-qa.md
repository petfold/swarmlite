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
