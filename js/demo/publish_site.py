"""Assemble and publish the browser demo as one immutable Swarm site.

    .venv/bin/python js/demo/publish_site.py --stamp <batchID> [--rows 30000]

Layout under the published root (everything relative, fully
self-contained — page, reader, wasm and database under one hash):

    index.html  app.js  demo.db
    src/{index.js, SwarmVFS.js}
    vendor/wa-sqlite/{dist/*, src/*}

Prints the site URL on the local gateway.
"""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

import fsspec

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "examples"))
from offline_demo import build  # noqa: E402


def assemble(site: Path, rows: int) -> None:
    js = REPO / "js"
    shutil.copy(js / "demo" / "index.html", site / "index.html")
    shutil.copy(js / "demo" / "app.js", site / "app.js")
    shutil.copytree(js / "src", site / "src")
    vendor = site / "vendor" / "wa-sqlite"
    shutil.copytree(js / "vendor" / "wa-sqlite" / "dist", vendor / "dist")
    shutil.copytree(js / "vendor" / "wa-sqlite" / "src", vendor / "src")
    (site / "demo.db").write_bytes(build(rows=rows))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stamp", default="auto")
    ap.add_argument("--api-url", dest="api_url")
    ap.add_argument("--rows", type=int, default=30_000)
    args = ap.parse_args()

    opts = {"stamp": args.stamp}
    if args.api_url:
        opts["api_url"] = args.api_url
    fs = fsspec.filesystem("bzz", **opts)

    with tempfile.TemporaryDirectory() as d:
        site = Path(d) / "site"
        site.mkdir()
        assemble(site, args.rows)
        total = sum(f.stat().st_size for f in site.rglob("*") if f.is_file())
        print(f"uploading {total / 2**20:.1f} MB site ...")
        root = fs.upload(str(site))

    api = args.api_url or "http://localhost:1633"
    print(f"root: {root}")
    print(f"site: {api}/bzz/{root}/index.html")
    print(f"db:   {api}/bzz/{root}/demo.db")
    return 0


if __name__ == "__main__":
    sys.exit(main())
