"""Read-only SQLite VFS over Swarm (via swarmfs).

The whole trick, from docs/DESIGN.md:

    application          plain SELECT
    SQLite engine        unmodified (apsw)
    bzz-VFS shim         xOpen / xRead / xFileSize      <- this module
    swarmfs file object  seek(off); read(n)
    Bee                  /bytes, HTTP range reads

A read-only VFS needs three real methods. SQLite's default page (4096 B)
equals a Swarm chunk, so with a VACUUMed, page_size=4096 database one page
read maps onto one chunk-aligned range request.

Hard constraints (CLAUDE.md): read-only — write methods raise; all
transport delegated to fsspec/swarmfs; journal/WAL files reported absent.

Status: v0 skeleton. Method contracts are final; bodies are not written.
"""

from __future__ import annotations

import apsw
import fsspec

VFS_NAME = "swarmlite"
DEFAULT_PAGE_CACHE = 1024  # pages (~4 MB at 4 KB/page), LRU


def connect(
    url: str,
    *,
    api_url: str | None = None,
    block_size: int = 2**16,
    cache_pages: int = DEFAULT_PAGE_CACHE,
    **storage_options,
) -> apsw.Connection:
    """Open a read-only connection to a SQLite file published on Swarm.

    ``url`` is either an immutable pin (``bzz://<ref>/site.db``) or a feed
    (``bzzf://<owner>/<topic>``, resolved to the latest published root at
    open time; the resolved root is recorded on the connection as
    ``con.swarmlite_root`` so callers can pin what they actually read).

    Registers :class:`SwarmVFS` on first use and opens with
    ``flags=SQLITE_OPEN_READONLY, vfs=VFS_NAME``.
    """
    raise NotImplementedError("v0: see docs/roadmap.md")


class SwarmVFS(apsw.VFS):
    """VFS whose 'filenames' are bzz:// / bzzf:// URLs.

    - ``xOpen``: resolve the URL via fsspec/swarmfs, capture file size,
      return a :class:`SwarmVFSFile`. Only main-database, read-only opens
      are honoured; journal/WAL/temp opens are answered per SQLite's
      read-only expectations.
    - ``xAccess``: the main file exists; ``*-journal`` / ``*-wal`` do not.
    - ``xDelete`` and friends: raise ``apsw.ReadOnlyError``.
    """

    def __init__(self, *, storage_options: dict | None = None):
        raise NotImplementedError("v0: see docs/roadmap.md")


class SwarmVFSFile(apsw.VFSFile):
    """One open, immutable remote database file.

    - ``xRead(amount, offset)``: serve from the LRU page cache keyed by
      ``(root, page_offset)``; on miss, ``seek``/``read`` on the swarmfs
      file object (which adds its own block cache / readahead).
    - ``xFileSize``: cached from open (the file is immutable).
    - ``xLock``/``xUnlock``/``xSync``: no-ops (immutable, read-only).
    - ``xWrite``/``xTruncate``: raise ``apsw.ReadOnlyError``.

    Testing hook: ``self.read_count`` / ``self.bytes_fetched`` counters so
    tests can assert the read budget (the page-economy claim is the
    product — test it).
    """

    def __init__(self, fileobj, size: int, cache_pages: int):
        raise NotImplementedError("v0: see docs/roadmap.md")
