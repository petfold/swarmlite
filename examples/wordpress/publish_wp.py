"""Assemble and publish a WordPress export as one immutable Swarm site.

    python publish_wp.py export.wxr [--stamp ID] [--feed TOPIC] [--signer HEX]

Layout under the published root (fully self-contained: theme + reader +
wasm + database — the whole blog is one hash):

    index.html  app.js  blog.db
    src/*  vendor/*        (the swarmlite JS reader)

With --feed, republishing after your next WordPress export keeps the
same bzzf:// URL for readers (and `swarmlite snapshots` lists every
past version of the blog).
"""

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

import fsspec

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from wp_export import build_db, parse_wxr  # noqa: E402


def assemble(site: Path, wxr_path: str) -> tuple[str, int]:
    here = Path(__file__).parent
    shutil.copy(here / "theme" / "index.html", site / "index.html")
    shutil.copy(here / "theme" / "app.js", site / "app.js")
    shutil.copytree(REPO / "js" / "src", site / "src")
    vendor = site / "vendor"
    for sub in ("wa-sqlite/dist", "wa-sqlite/src", "js-sha3", "noble-secp256k1"):
        shutil.copytree(REPO / "js" / "vendor" / sub, vendor / sub)
    title, posts = parse_wxr(wxr_path)
    build_db(title, posts, str(site / "blog.db"))
    return title, len(posts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("wxr")
    ap.add_argument("--stamp", default="auto")
    ap.add_argument("--api-url", dest="api_url")
    ap.add_argument("--feed", help="feed topic for a stable, updatable URL")
    ap.add_argument("--signer", help="feed owner key (or $SWARMLITE_SIGNER)")
    args = ap.parse_args()

    opts = {"stamp": args.stamp}
    if args.api_url:
        opts["api_url"] = args.api_url

    with tempfile.TemporaryDirectory() as d:
        site = Path(d) / "site"
        site.mkdir()
        title, n = assemble(site, args.wxr)
        print(f"'{title}': {n} posts")
        if args.feed:
            signer = args.signer or os.environ.get("SWARMLITE_SIGNER")
            if not signer:
                print("feed publishing needs --signer or $SWARMLITE_SIGNER",
                      file=sys.stderr)
                return 1
            from swarmfs.feeds import FeedSigner
            owner = FeedSigner(signer).owner_hex
            fs = fsspec.filesystem("bzzf", signer=signer, **opts)
            fs.upload(str(site), f"bzzf://{owner}/{args.feed}")
            root = fs.latest(f"bzzf://{owner}/{args.feed}")
            print(f"feed: bzzf://{owner}/{args.feed}/index.html")
        else:
            fs = fsspec.filesystem("bzz", **opts)
            root = fs.upload(str(site))

    api = args.api_url or "http://localhost:1633"
    print(f"root: {root}")
    print(f"site: {api}/bzz/{root}/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
