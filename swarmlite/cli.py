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

        con = connect(args.url, api_url=args.api_url)
        for row in con.execute(args.sql):
            print("\t".join(str(v) for v in row))
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
