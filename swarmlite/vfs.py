"""Read-only SQLite VFS over Swarm (via swarmfs / fsspec).

The whole trick, from docs/DESIGN.md:

    application          plain SELECT
    SQLite engine        unmodified (apsw)
    bzz-VFS shim         xOpen / xRead / xFileSize      <- this module
    fsspec file object   seek(off); read(n)             (swarmfs for bzz://)
    Bee                  /bytes, HTTP range reads

A read-only VFS needs three real methods. SQLite's default page (4096 B)
equals a Swarm chunk, so with a VACUUMed, page_size=4096 database one page
read maps onto one chunk-aligned range request.

Hard constraints (CLAUDE.md): read-only — write methods raise; all
transport delegated to fsspec/swarmfs; journal/WAL files reported absent.

Transport-agnostic by design: any fsspec URL works (``bzz://``, ``bzzf://``,
``file://``, ``memory://`` ...), which is also how the offline tests run.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

import apsw
import fsspec

VFS_NAME = "swarmlite"
PAGE_SIZE = 4096  # SQLite default page == Swarm chunk
DEFAULT_PAGE_CACHE = 1024  # pages (~4 MiB), LRU

_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")

# connect() -> xOpen handshake. connect() stashes per-URL options here and
# collects the opened file object back; xOpen only honours URLs that went
# through connect(). Guarded by _lock. (Concurrent connect() calls to the
# *same* URL are not supported; different URLs are fine.)
_lock = threading.Lock()
_pending: dict[str, dict] = {}
_opened: dict[str, "SwarmVFSFile"] = {}
_vfs_singleton: "SwarmVFS | None" = None


class SwarmliteConnection(apsw.Connection):
    """apsw.Connection carrying the VFS file handle for introspection.

    ``swarmlite_file`` exposes the read counters (``read_count``,
    ``pages_fetched``, ``bytes_fetched``) — the page-economy numbers that
    are the point of this package. ``swarmlite_url`` is the URL as opened.
    """

    swarmlite_file: "SwarmVFSFile"
    swarmlite_url: str


def connect(
    url: str,
    *,
    cache_pages: int = DEFAULT_PAGE_CACHE,
    **storage_options,
) -> SwarmliteConnection:
    """Open a read-only connection to a SQLite file published on Swarm.

    ``url`` is any fsspec URL: an immutable pin (``bzz://<ref>/site.db``),
    a feed (``bzzf://<owner>/<topic>``, resolved to the latest published
    version at open time), or — mainly for tests and local work —
    ``file://`` / ``memory://``. ``storage_options`` are passed to the
    fsspec filesystem (for ``bzz://`` that means swarmfs options such as
    ``api_url``, ``block_size``, ``allow_gateway``).

    The connection is strictly read-only; DML raises
    ``apsw.ReadOnlyError``. ``temp_store`` is forced to memory so sort
    spills never try to open temp files on the (read-only) VFS.
    """
    global _vfs_singleton
    if "://" not in url:
        raise ValueError(
            f"swarmlite.connect() needs a URL like bzz://<ref>/site.db, "
            f"got {url!r} (for a local file use file://...)"
        )
    with _lock:
        if _vfs_singleton is None:
            _vfs_singleton = SwarmVFS()
        if url in _pending:
            raise RuntimeError(f"concurrent connect() to the same URL: {url!r}")
        _pending[url] = {
            "cache_pages": cache_pages,
            "storage_options": storage_options,
        }
    try:
        con = SwarmliteConnection(
            url, flags=apsw.SQLITE_OPEN_READONLY, vfs=VFS_NAME
        )
    finally:
        with _lock:
            _pending.pop(url, None)
            handle = _opened.pop(url, None)
    if handle is None:  # defensive: xOpen must have run for the main db
        con.close()
        raise RuntimeError(f"VFS did not open {url!r} (no file handle recorded)")
    con.swarmlite_file = handle
    con.swarmlite_url = url
    # sorting/materialization must spill to RAM, never to a temp file on
    # this read-only VFS
    con.execute("PRAGMA temp_store = MEMORY")
    return con


class SwarmVFS(apsw.VFS):
    """VFS whose 'filenames' are fsspec URLs.

    Inherits the process-default VFS (base ``""``) for the incidental
    methods (randomness, time, sleep); overrides everything path-shaped so
    URLs are never treated as OS paths.
    """

    def __init__(self) -> None:
        super().__init__(VFS_NAME, base="")

    def xFullPathname(self, name: str) -> str:
        return name  # URLs are already canonical; never realpath() them

    def xAccess(self, pathname: str, flags: int) -> bool:
        if pathname.endswith(_SIDECAR_SUFFIXES):
            return False  # journal/WAL/shm never exist for a published db
        if flags == apsw.SQLITE_ACCESS_READWRITE:
            return False  # nothing here is writable
        return True

    def xDelete(self, filename: str, syncdir: bool) -> None:
        # Only ever called for sidecar files, which don't exist; deleting
        # a published database is meaningless.
        if filename.endswith(_SIDECAR_SUFFIXES):
            return
        raise apsw.ReadOnlyError()

    def xOpen(self, name, flags: list) -> "SwarmVFSFile":
        if name is None:
            # SQLite wants a temp file (sort spill). connect() sets
            # temp_store=MEMORY, so this only happens on misuse.
            raise apsw.CantOpenError(
                "swarmlite VFS is read-only and cannot create temp files; "
                "run PRAGMA temp_store=MEMORY (swarmlite.connect does)"
            )
        url = name.filename() if hasattr(name, "filename") else str(name)
        if not (flags[0] & apsw.SQLITE_OPEN_MAIN_DB):
            raise apsw.CantOpenError(
                f"swarmlite VFS only serves main database files, "
                f"refused open of {url!r}"
            )
        with _lock:
            opts = _pending.get(url)
        if opts is None:
            raise apsw.CantOpenError(
                f"{url!r} was not opened through swarmlite.connect()"
            )
        fs, path = fsspec.core.url_to_fs(url, **opts["storage_options"])
        fileobj = fs.open(path, "rb")
        size = getattr(fileobj, "size", None)
        if size is None:
            size = fs.size(path)
        handle = SwarmVFSFile(fileobj, int(size), opts["cache_pages"], url=url)
        with _lock:
            _opened[url] = handle
        flags[1] = flags[0]
        return handle


class SwarmVFSFile:
    """One open, immutable remote database file (duck-typed VFS file).

    ``xRead`` serves from an LRU cache of 4 KiB pages; misses are fetched
    with one backend ``seek``/``read`` per *contiguous* missing run, so a
    linear scan costs few backend calls while point lookups touch single
    pages. The underlying fsspec file adds its own block cache/readahead
    (swarmfs's, for ``bzz://``).

    Read counters — the page-economy numbers this package exists for:

    - ``read_count``    backend read calls issued
    - ``pages_fetched`` 4 KiB pages pulled from the backend
    - ``bytes_fetched`` bytes pulled from the backend

    The file is immutable (content-addressed), so locking is a no-op and
    ``xDeviceCharacteristics`` advertises ``SQLITE_IOCAP_IMMUTABLE`` —
    SQLite then skips change detection entirely.
    """

    def __init__(self, fileobj, size: int, cache_pages: int, url: str = ""):
        self._f = fileobj
        self._size = size
        self._cap = max(1, int(cache_pages))
        self._cache: OrderedDict[int, bytes] = OrderedDict()
        self._mu = threading.Lock()
        self.url = url
        self.read_count = 0
        self.pages_fetched = 0
        self.bytes_fetched = 0

    # -- stats -----------------------------------------------------------
    @property
    def size(self) -> int:
        return self._size

    def stats(self) -> dict:
        return {
            "url": self.url,
            "file_size": self._size,
            "read_count": self.read_count,
            "pages_fetched": self.pages_fetched,
            "bytes_fetched": self.bytes_fetched,
            "cached_pages": len(self._cache),
        }

    # -- the three methods that matter ------------------------------------
    def xRead(self, amount: int, offset: int) -> bytes:
        with self._mu:
            if amount <= 0 or offset >= self._size:
                return b""
            end = min(offset + amount, self._size)
            first = offset // PAGE_SIZE
            last = (end - 1) // PAGE_SIZE

            pages: dict[int, bytes] = {}
            for i in range(first, last + 1):
                cached = self._cache.get(i)
                if cached is not None:
                    self._cache.move_to_end(i)
                    pages[i] = cached

            i = first
            while i <= last:  # fetch each contiguous missing run in one read
                if i in pages:
                    i += 1
                    continue
                j = i
                while j <= last and j not in pages:
                    j += 1
                start_off = i * PAGE_SIZE
                end_off = min(j * PAGE_SIZE, self._size)
                self._f.seek(start_off)
                blob = self._f.read(end_off - start_off)
                self.read_count += 1
                self.bytes_fetched += len(blob)
                for k in range(i, j):
                    page = blob[(k - i) * PAGE_SIZE : (k - i + 1) * PAGE_SIZE]
                    pages[k] = page
                    self._cache[k] = page
                    self._cache.move_to_end(k)
                    self.pages_fetched += 1
                i = j

            while len(self._cache) > self._cap:
                self._cache.popitem(last=False)

            data = b"".join(pages[i] for i in range(first, last + 1))
            skip = offset - first * PAGE_SIZE
            return data[skip : skip + (end - offset)]

    def xFileSize(self) -> int:
        return self._size

    def xClose(self) -> None:
        try:
            self._f.close()
        except Exception:
            pass

    # -- immutable/read-only boilerplate -----------------------------------
    def xLock(self, level: int) -> None:
        pass

    def xUnlock(self, level: int) -> None:
        pass

    def xCheckReservedLock(self) -> bool:
        return False

    def xSync(self, flags: int) -> None:
        pass

    def xSectorSize(self) -> int:
        return PAGE_SIZE

    def xDeviceCharacteristics(self) -> int:
        return apsw.SQLITE_IOCAP_IMMUTABLE

    def xFileControl(self, op: int, ptr: int) -> bool:
        return False

    def xWrite(self, data, offset: int) -> None:
        raise apsw.ReadOnlyError()

    def xTruncate(self, newsize: int) -> None:
        raise apsw.ReadOnlyError()
