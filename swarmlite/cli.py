"""swarmlite command line.

    swarmlite publish site.db [--feed TOPIC] [--stamp ID] [--name FILE]
    swarmlite query URL SQL        # smoke-test a published database

Bee endpoint / signer come from flags or the swarmfs conventions
(BEE_API_URL; signer key via env/keystore — decide in v1).
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="swarmlite",
        description="Verifiable serverless SQLite hosting on Swarm.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_pub = sub.add_parser("publish", help="publish a local SQLite file")
    p_pub.add_argument("db_path")
    p_pub.add_argument("--name", default="site.db")
    p_pub.add_argument("--feed", help="feed topic to advance to the new root")
    p_pub.add_argument("--stamp", default="auto")
    p_pub.add_argument("--api-url", dest="api_url")

    p_q = sub.add_parser("query", help="run SQL against a published database")
    p_q.add_argument("url", help="bzz://<ref>/file.db or bzzf://<owner>/<topic>")
    p_q.add_argument("sql")
    p_q.add_argument("--api-url", dest="api_url")
    p_q.add_argument(
        "--block-size", dest="block_size", type=int, default=65536,
        help="transport readahead block in bytes (default 64 KiB; "
        "larger helps scans, smaller helps point lookups)",
    )
    p_q.add_argument(
        "--stats", action="store_true",
        help="print pages/bytes fetched vs. file size to stderr",
    )

    args = parser.parse_args(argv)

    if args.command == "publish":
        from .publish import publish

        root = publish(
            args.db_path, name=args.name, feed=args.feed,
            stamp=args.stamp, api_url=args.api_url,
        )
        print(root)
        return 0

    if args.command == "query":
        from .vfs import connect

        opts = {"api_url": args.api_url} if args.api_url else {}
        if args.block_size:
            opts["block_size"] = args.block_size
        con = connect(args.url, **opts)
        for row in con.execute(args.sql):
            print("\t".join(str(v) for v in row))
        if args.stats:
            s = con.swarmlite_file.stats()
            print(
                f"fetched {s['pages_fetched']} pages "
                f"({s['bytes_fetched'] / 1024:.0f} KB) "
                f"in {s['read_count']} reads, "
                f"of a {s['file_size'] / 2**20:.1f} MB file",
                file=sys.stderr,
            )
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
