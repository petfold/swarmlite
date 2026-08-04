"""The publisher: the only write path (docs/DESIGN.md section 3).

The checklist, encoded in :func:`prepare` (keep in sync with CLAUDE.md):

    copy the database (never mutate the caller's file; backup API, so a
      live WAL source is checkpointed correctly)
    PRAGMA journal_mode=DELETE   -- no WAL sidecar in the artifact
    PRAGMA page_size=4096        -- align pages to Swarm chunks
    warn about large tables with no index (caller's job to fix)
    ANALYZE                      -- give the remote query planner statistics
    VACUUM                       -- contiguous layout; applies the page size
    PRAGMA integrity_check       -- must be 'ok'

then :func:`publish` uploads inside a swarmfs transaction:

    without a feed: bzz://new/<name>  ->  immutable pin root
    with a feed:    bzzf://<owner>/<topic>/<name>  ->  the same commit
                    machinery plus a signed feed update; one upload yields
                    BOTH the stable feed URL and the immutable pin root.

Every publish is a permanent snapshot; the feed names "latest".
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import fsspec

PAGE_SIZE = 4096
LARGE_TABLE_ROWS = 5000  # tables at/above this without an index draw a warning
SIGNER_ENV = "SWARMLITE_SIGNER"


class PublishError(RuntimeError):
    """The database failed a check that must not be papered over."""


def prepare(db_path: str | os.PathLike, out_path: str | os.PathLike) -> list[str]:
    """Produce a publish-ready copy of ``db_path`` at ``out_path``.

    Runs the publisher's checklist on the copy; the source is never
    touched. Returns human-readable warnings for conditions that were
    fixed silently (page size, WAL) or that the caller should fix
    (missing indexes). Raises :class:`PublishError` if the database fails
    ``integrity_check``.
    """
    db_path, out_path = Path(db_path), Path(out_path)
    if not db_path.exists():
        raise FileNotFoundError(
            f"no such database file: '{db_path}' — publish uploads an "
            f"existing local SQLite file. To try the flow with demo data, "
            f"build one first:  python -c \"import sys; "
            f"sys.path.insert(0, 'examples'); from offline_demo import "
            f"build; open('demo.db', 'wb').write(build(rows=30000))\"  "
            f"then publish demo.db"
        )
    warnings: list[str] = []

    # backup-API copy: consistent even if the source is a live WAL db
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(out_path)
    try:
        src.backup(dst)
    finally:
        src.close()

    try:
        (mode,) = dst.execute("PRAGMA journal_mode").fetchone()
        if mode.lower() != "delete":
            dst.execute("PRAGMA journal_mode=DELETE")
            if mode.lower() == "wal":
                warnings.append(
                    "journal_mode was WAL; switched to DELETE for the artifact"
                )

        (page_size,) = dst.execute("PRAGMA page_size").fetchone()
        if page_size != PAGE_SIZE:
            dst.execute(f"PRAGMA page_size={PAGE_SIZE}")
            warnings.append(
                f"page_size was {page_size}; rewriting to {PAGE_SIZE} "
                f"(one page = one Swarm chunk)"
            )

        # ordinary tables only: 'shadow' skips virtual-table internals
        # (FTS5's _docsize etc. — rowid-accessed by the extension), and
        # wr=1 (WITHOUT ROWID) means the PRIMARY KEY *is* the table's
        # B-tree, so leading-PK-column filters are index searches
        for schema, table, ttype, _ncol, wr, _strict in dst.execute(
            "PRAGMA table_list"
        ).fetchall():
            if schema != "main" or ttype != "table" or wr:
                continue
            if table.startswith("sqlite_"):
                continue
            (n,) = dst.execute(f'SELECT count(*) FROM "{table}"').fetchone()
            (idx,) = dst.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='index' "
                "AND tbl_name=?",
                (table,),
            ).fetchone()
            if n >= LARGE_TABLE_ROWS and idx == 0:
                warnings.append(
                    f"table {table!r} has {n} rows and no index — remote "
                    f"queries filtering on non-rowid columns will scan the "
                    f"whole file (INTEGER PRIMARY KEY lookups are still fine)"
                )

        dst.execute("ANALYZE")
        dst.commit()
        dst.execute("VACUUM")  # applies the page size, defragments

        result = [r[0] for r in dst.execute("PRAGMA integrity_check").fetchall()]
        if result != ["ok"]:
            raise PublishError(
                f"integrity_check failed for {db_path}: {result[:5]}"
            )
    finally:
        dst.close()
    return warnings


def _filesystem(protocol: str, **opts):
    try:
        return fsspec.filesystem(protocol, **opts)
    except TypeError as e:
        if "encrypt" in str(e):
            raise PublishError(
                "encrypt=True needs swarmfs >= 0.9 (the encrypted-storage "
                "release): pip install -U swarmfs"
            ) from e
        raise


def publish(
    db_path: str | os.PathLike,
    *,
    name: str | None = None,
    feed: str | None = None,
    stamp: str = "auto",
    signer: str | None = None,
    api_url: str | None = None,
    encrypt: bool = False,
    quiet: bool = False,
) -> str:
    """Publish a local SQLite file to Swarm; return the immutable root.

    Runs :func:`prepare` on a temporary copy, then uploads it inside a
    swarmfs transaction. Without ``feed``, the result is an immutable pin
    (``bzz://<root>/<name>``). With ``feed`` (a topic string), the upload
    goes through the ``bzzf://`` filesystem instead: the same single
    upload also advances the owner's feed, so readers get a stable URL
    (``bzzf://<owner>/<feed>/<name>``) *and* the pin stays valid. Feed
    publishing needs ``signer`` (the owner's private key hex; falls back
    to ``$SWARMLITE_SIGNER``) and the swarmfs ``feeds`` extra.

    With ``encrypt``, everything — pages and manifest alike — is
    encrypted node-side (swarmfs ≥ 0.9): the returned root is 128 hex
    (address + decryption key), so **the URL itself is the secret** —
    whoever holds the full ``bzz://`` or feed-resolved reference reads
    plaintext, everyone else stores noise. Readers need no flag; the node
    decrypts in the load path, and feeds carry the full reference. Note
    an encrypted upload stamps more chunks (size the batch with
    ``encrypted=True`` / ``--encrypt``).

    Warnings from the checklist go to stderr unless ``quiet``.
    ``name`` is the file name inside the published manifest; it defaults
    to the source file's own name.
    """
    name = name or os.path.basename(os.fspath(db_path))
    fs_opts: dict = {"stamp": stamp}
    if api_url:
        fs_opts["api_url"] = api_url
    if encrypt:
        fs_opts["encrypt"] = True

    workdir = tempfile.mkdtemp(prefix="swarmlite-publish-")
    try:
        prepared = os.path.join(workdir, name)
        for w in prepare(db_path, prepared):
            if not quiet:
                print(f"warning: {w}", file=sys.stderr)

        if feed:
            signer = signer or os.environ.get(SIGNER_ENV)
            if not signer:
                raise PublishError(
                    f"feed publishing needs a signer key: pass signer=... "
                    f"or set ${SIGNER_ENV}"
                )
            from swarmfs.feeds import FeedSigner  # needs swarmfs[feeds]

            owner = FeedSigner(signer).owner_hex
            fs = _filesystem("bzzf", signer=signer, **fs_opts)
            feed_base = f"bzzf://{owner}/{feed}"
            with fs.transaction:
                fs.put_file(str(prepared), f"{feed_base}/{name}")
            root = fs.latest(feed_base)
            if not quiet:
                print(f"pin:  bzz://{root}/{name}", file=sys.stderr)
                print(f"feed: {feed_base}/{name}", file=sys.stderr)
        else:
            fs = _filesystem("bzz", **fs_opts)
            with fs.transaction:
                fs.put_file(str(prepared), f"bzz://new/{name}")
            root = fs.latest("new")
            if not quiet:
                print(f"pin:  bzz://{root}/{name}", file=sys.stderr)
                # whether the data will change is publishing intent — it
                # cannot be read off the file, so say it where it matters
                print(
                    "tip:  this URL names exactly this version. If the "
                    "data will change, republish with --feed <topic> so "
                    "readers get one stable bzzf:// URL (User Guide §5)",
                    file=sys.stderr,
                )
        return root
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
