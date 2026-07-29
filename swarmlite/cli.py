"""swarmlite command line.

    swarmlite publish site.db [--feed TOPIC] [--stamp ID] [--name FILE]
    swarmlite query URL SQL        # smoke-test a published database
    swarmlite snapshots FEED_URL   # list every version a feed published
    swarmlite stamps               # batches, their life left, and headroom
    swarmlite stamps --check       # exit non-zero when one needs renewing
    swarmlite stamps topup ID --for 4w    # extend a batch (spends xBZZ)
    swarmlite stamps dilute ID --depth 20 # more capacity, costs TTL

Bee endpoint / signer come from flags or the swarmfs conventions
(BEE_API_URL; signer key via env/keystore — decide in v1).
"""

from __future__ import annotations

import argparse
import os
import re
import sys

_PLACEHOLDER = re.compile(r"<[^<> ]+>")


def _reject_placeholders(**values: str | None) -> None:
    """Fail early, with advice, when a docs placeholder like <root> was
    copy-pasted verbatim instead of the real value."""
    for name, value in values.items():
        if value and (m := _PLACEHOLDER.search(value)):
            raise ValueError(
                f"{name} contains the placeholder {m.group(0)} — replace it "
                f"with the real value (the root/reference and feed URL are "
                f"printed by 'swarmlite publish'; stamp batch IDs by "
                f"'curl $BEE_API_URL/stamps')"
            )


def _stamp_error() -> type[Exception]:
    from swarmfs.exceptions import StampError

    return StampError


def _api_url(args: argparse.Namespace) -> str:
    return args.api_url or os.environ.get("BEE_API_URL") or "http://localhost:1633"


def _confirm(what: str, prompt: str, yes: bool) -> None:
    """Gate a wallet-spending action on an explicit human yes."""
    if yes:
        return
    if not sys.stdin.isatty():
        raise ValueError(
            f"{what} needs an interactive terminal to confirm; pass --yes "
            "to skip the prompt"
        )
    if input(f"{prompt} [y/N] ").strip().lower() not in ("y", "yes"):
        raise ValueError("declined; nothing was spent")


def _fmt_ttl(secs: int) -> str:
    if secs < 0:
        return "unknown"
    if secs >= 86400:
        return f"{secs / 86400:.1f} d"
    return f"{secs / 3600:.1f} h"


def _buy_stamp(args: argparse.Namespace) -> str:
    """Plan, confirm, and buy a batch sized for the file. Returns the
    usable batch id."""
    from .stamps import buy_batch, parse_ttl, plan_batch

    if args.stamp != "auto":
        raise ValueError("--buy and --stamp are mutually exclusive — "
                         "--buy always purchases a fresh batch")
    size = os.path.getsize(args.db_path)
    api_url = _api_url(args)
    plan = plan_batch(size, parse_ttl(args.ttl), api_url)
    print(
        f"batch for {size / 2**20:.1f} MB: depth {plan.depth}, "
        f"amount {plan.amount}, lasting ~{plan.ttl_secs / 3600:.0f} h "
        f"-> {plan.cost_bzz:.4f} xBZZ from the node's wallet",
        file=sys.stderr,
    )
    _confirm("--buy", "buy it?", args.yes)
    print("buying (waits for on-chain confirmation) ...", file=sys.stderr)
    stamp = buy_batch(api_url, plan.amount, plan.depth)
    print(f"bought batch {stamp}", file=sys.stderr)
    return stamp


def _stamps_list(args: argparse.Namespace) -> int:
    """Show every batch with the two numbers that decide a publication's
    fate: how long it has left, and how close its fullest bucket is."""
    from .stamps import list_batches, parse_ttl

    api_url = _api_url(args)
    batches = list_batches(api_url)
    if not batches:
        print(f"swarmlite: no postage batches on {api_url}", file=sys.stderr)
        return 1

    min_ttl = parse_ttl(args.min_ttl)
    print(f"{'batch':10} {'depth':>5} {'life left':>10} {'fullest bucket':>15} status")
    failing = []
    for b in sorted(batches, key=lambda b: b.ttl):
        problem = b.problem(min_ttl)
        used = f"{b.utilization}/{b.bucket_capacity}" if b.utilization is not None else "?"
        if b.utilization_ratio is not None:
            used += f" ({b.utilization_ratio:.0%})"
        # swarmfs states TTL problems in seconds; the table speaks days
        status = problem or "ok"
        if problem and problem.startswith("TTL "):
            status = f"needs renewing (under {args.min_ttl})"
        print(f"{b.batch_id[:8]}…  {b.depth:>5} {_fmt_ttl(b.ttl):>10} {used:>15} "
              f"{status}")
        if problem:
            failing.append((b, problem))

    if args.check:
        for b, problem in failing:
            print(f"swarmlite: batch {b.batch_id[:8]}… {problem} — "
                  f"'swarmlite stamps topup {b.batch_id} --for 4w' while it "
                  "still lives (an expired batch cannot be revived)",
                  file=sys.stderr)
        return 1 if failing else 0
    return 0


def _stamps_topup(args: argparse.Namespace) -> int:
    """Extend a batch: the renewal a published root's life depends on."""
    from .stamps import parse_ttl, plan_topup, topup_batch

    api_url = _api_url(args)
    _reject_placeholders(batch=args.batch, api_url=args.api_url)
    if args.for_ttl:
        target = {"ttl_secs": parse_ttl(args.for_ttl)}
    elif args.to_ttl:
        target = {"total_ttl_secs": parse_ttl(args.to_ttl)}
    else:
        target = {"budget_bzz": args.budget}

    plan = plan_topup(api_url, args.batch, **target)
    print(
        f"topping up {plan.batch_id[:8]}… (depth {plan.depth}): "
        f"+{_fmt_ttl(plan.added_ttl_secs)} for {plan.cost_bzz:.4f} xBZZ "
        f"-> {_fmt_ttl(plan.total_ttl_secs)} of life "
        f"(amount {plan.added_amount})",
        file=sys.stderr,
    )
    if plan.warning:
        print(f"warning: {plan.warning}", file=sys.stderr)
    _confirm("topup", "spend it?", args.yes)
    print("topping up (the node takes ~40 s to apply it) ...", file=sys.stderr)
    info = topup_batch(api_url, args.batch, plan.added_amount)
    print(f"batch {info.batch_id[:8]}… now has {_fmt_ttl(info.ttl)} left",
          file=sys.stderr)
    print(info.ttl)
    return 0


def _stamps_dilute(args: argparse.Namespace) -> int:
    """Raise a batch's capacity. Paid for in TTL, not xBZZ."""
    from .stamps import dilute_batch, plan_dilute

    api_url = _api_url(args)
    _reject_placeholders(batch=args.batch, api_url=args.api_url)
    plan = plan_dilute(api_url, args.batch, args.depth)
    print(
        f"diluting {plan.batch_id[:8]}… from depth {plan.from_depth} to "
        f"{plan.to_depth}: capacity per bucket x{2 ** (plan.to_depth - plan.from_depth)}, "
        f"life {_fmt_ttl(plan.ttl_before_secs)} -> {_fmt_ttl(plan.ttl_after_secs)} "
        "(gas only, but the same balance now covers more chunks)",
        file=sys.stderr,
    )
    if plan.warning:
        print(f"warning: {plan.warning}", file=sys.stderr)
    _confirm("dilute", "dilute it?", args.yes)
    print("diluting (the node takes ~40 s to apply it) ...", file=sys.stderr)
    info = dilute_batch(api_url, args.batch, args.depth)
    print(f"batch {info.batch_id[:8]}… is now depth {info.depth} with "
          f"{_fmt_ttl(info.ttl)} left — top up to restore the halved life",
          file=sys.stderr)
    print(info.depth)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(argv)
    except Exception as e:  # CLI boundary: one actionable line, no traceback
        if os.environ.get("SWARMLITE_DEBUG"):
            raise
        print(f"swarmlite: {e}", file=sys.stderr)
        return 1


def _run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="swarmlite",
        description="Verifiable serverless SQLite hosting on Swarm.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_pub = sub.add_parser("publish", help="publish a local SQLite file")
    p_pub.add_argument("db_path")
    p_pub.add_argument(
        "--name",
        help="file name inside the published manifest "
        "(default: the source file's name)",
    )
    p_pub.add_argument("--feed", help="feed topic to advance to the new root")
    p_pub.add_argument(
        "--signer",
        help="feed owner's private key hex (default: $SWARMLITE_SIGNER)",
    )
    p_pub.add_argument("--stamp", default="auto")
    p_pub.add_argument("--api-url", dest="api_url")
    p_pub.add_argument(
        "--buy", action="store_true",
        help="buy a new postage batch sized for the file from the node's "
        "wallet (shows the xBZZ cost and asks first)",
    )
    p_pub.add_argument(
        "--ttl", default="1d",
        help="how long the bought batch should last, e.g. 36h/7d/4w "
        "(default 1d; the node enforces a 24h minimum)",
    )
    p_pub.add_argument(
        "--yes", action="store_true",
        help="skip the purchase confirmation (for non-interactive use)",
    )

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

    p_st = sub.add_parser(
        "stamps", help="list, monitor, and renew postage batches"
    )
    p_st.add_argument("--api-url", dest="api_url")
    p_st.add_argument(
        "--check", action="store_true",
        help="exit non-zero if any batch is below --min-ttl (cron-friendly: "
        "an expired batch cannot be revived, so warn early)",
    )
    p_st.add_argument(
        "--min-ttl", dest="min_ttl", default="7d",
        help="renewal threshold for the status column and --check "
        "(default 7d)",
    )
    st_sub = p_st.add_subparsers(dest="stamps_cmd")

    p_top = st_sub.add_parser("topup", help="extend a batch's life (spends xBZZ)")
    p_top.add_argument("batch", help="batch id ('swarmlite stamps' lists them)")
    target = p_top.add_mutually_exclusive_group(required=True)
    target.add_argument("--for", dest="for_ttl", metavar="TTL",
                        help="extend BY this long, e.g. 36h/2w")
    target.add_argument("--to", dest="to_ttl", metavar="TTL",
                        help="extend TO this much remaining life")
    target.add_argument("--budget", type=float, metavar="XBZZ",
                        help="spend at most this many xBZZ")
    p_top.add_argument("--yes", action="store_true",
                       help="skip the confirmation (for non-interactive use)")
    p_top.add_argument("--api-url", dest="api_url", default=argparse.SUPPRESS)

    p_dil = st_sub.add_parser(
        "dilute", help="raise a batch's depth for more capacity (costs TTL)"
    )
    p_dil.add_argument("batch")
    p_dil.add_argument("--depth", type=int, required=True,
                       help="new depth (must exceed the current one)")
    p_dil.add_argument("--yes", action="store_true")
    p_dil.add_argument("--api-url", dest="api_url", default=argparse.SUPPRESS)

    p_s = sub.add_parser(
        "snapshots", help="list every version a feed has published"
    )
    p_s.add_argument("url", help="bzzf://<owner>/<topic>[/file.db]")
    p_s.add_argument("--api-url", dest="api_url")
    p_s.add_argument(
        "--verify", action="store_true",
        help="signature-check every update (needs swarmfs[feeds])",
    )

    args = parser.parse_args(argv)

    if args.command == "publish":
        from .publish import publish

        _reject_placeholders(
            db_path=args.db_path, name=args.name, feed=args.feed,
            signer=args.signer, stamp=args.stamp, api_url=args.api_url,
        )
        stamp = args.stamp
        if args.buy:
            stamp = _buy_stamp(args)
        try:
            root = publish(
                args.db_path, name=args.name, feed=args.feed,
                signer=args.signer, stamp=stamp, api_url=args.api_url,
            )
        except _stamp_error() as e:
            raise type(e)(
                f"{e} — or let swarmlite buy one sized for the file: "
                f"re-run with --buy (spends the node wallet's xBZZ; "
                f"services such as Beeport sell stamps if the wallet "
                f"has none)"
            ) from None
        print(root)
        return 0

    if args.command == "query":
        from .vfs import connect

        _reject_placeholders(url=args.url, api_url=args.api_url)
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

    if args.command == "stamps":
        cmd = getattr(args, "stamps_cmd", None)
        if cmd == "topup":
            return _stamps_topup(args)
        if cmd == "dilute":
            return _stamps_dilute(args)
        return _stamps_list(args)

    if args.command == "snapshots":
        from .snapshots import parse_feed_url, snapshots

        _reject_placeholders(url=args.url, api_url=args.api_url)
        opts = {"api_url": args.api_url} if args.api_url else {}
        _, _, fname = parse_feed_url(args.url)
        snaps = snapshots(args.url, verify=args.verify, **opts)
        if not snaps:
            print("swarmlite: feed has no updates yet", file=sys.stderr)
            return 1
        suffix = f"/{fname}" if fname else ""
        for s in snaps:
            when = (
                s.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
                if s.timestamp else "-"
            )
            pin = f"bzz://{s.root}{suffix}" if s.root else "(not retrievable yet)"
            mark = "   <- latest" if s.index == snaps[-1].index else ""
            print(f"{s.index}\t{when}\t{pin}{mark}")
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
