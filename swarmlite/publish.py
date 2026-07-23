"""The publisher: the only write path (docs/DESIGN.md section 3).

Checklist encoded here (keep in sync with CLAUDE.md):

    PRAGMA page_size=4096;        -- align pages to chunks (rewrite if not)
    PRAGMA journal_mode=DELETE;   -- no WAL sidecar in the artifact
    -- caller creates indexes / runs ANALYZE (warn if obviously missing)
    VACUUM;                       -- contiguous layout -> readahead works
    PRAGMA integrity_check;
    upload via fs.transaction     -- swarmfs; all-or-nothing
    optionally advance bzzf:// feed to the new root

Every publish is a permanent snapshot; the feed names "latest".

Status: v1 skeleton. Signature is final; body is not written.
"""

from __future__ import annotations


def publish(
    db_path: str,
    *,
    name: str = "site.db",
    feed: str | None = None,
    stamp: str = "auto",
    signer: str | None = None,
    api_url: str | None = None,
) -> str:
    """Publish a local SQLite file to Swarm; return the immutable root.

    Runs the pragma/VACUUM/integrity checklist on a *copy* of ``db_path``
    (never mutate the caller's working database), uploads it inside a
    swarmfs transaction, and — if ``feed`` is given (``"<topic>"`` with a
    ``signer`` key) — advances the feed to the new root.

    Prints (and returns) the pin URL; prints the feed URL when advanced.
    Warnings, not failures, for fixable issues it corrected (page size,
    WAL mode, missing ANALYZE).
    """
    raise NotImplementedError("v1: see docs/roadmap.md")
