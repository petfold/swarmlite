"""WordPress -> Swarm: turn a standard WXR export into a swarmlite blog.

    python wp_export.py export.wxr blog.db

Input is the file every WordPress site can produce (Tools -> Export ->
All content) — no database access, no plugins, works for wordpress.com
exports too. Output is a publish-ready SQLite database:

    posts(id, slug, title, author, ts, tags, html)   -- the blog itself
    kw(word, ts, id)                                 -- covering search index

The theme in theme/ renders posts client-side from this database via
the swarmlite reader, so the whole blog (pages included) is ONE
queryable file behind a static page. Media referenced by absolute URL
in post HTML keeps pointing at its original host — mirroring media into
the manifest is a natural follow-up, out of scope here.
"""

from __future__ import annotations

import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "wp": "http://wordpress.org/export/1.2/",
}

WORD = re.compile(r"[a-z0-9][a-z0-9'-]{2,}")
TAGS = re.compile(r"<[^>]+>")


def parse_wxr(path: str) -> tuple[str, list[dict]]:
    """(blog title, published posts) out of a WXR file. Pages,
    attachments, drafts and revisions are skipped."""
    channel = ET.parse(path).getroot().find("channel")
    blog_title = channel.findtext("title") or "Blog"
    posts = []
    for item in channel.findall("item"):
        if item.findtext("wp:post_type", namespaces=NS) != "post":
            continue
        if item.findtext("wp:status", namespaces=NS) != "publish":
            continue
        date = item.findtext("wp:post_date_gmt", namespaces=NS) or ""
        try:
            ts = int(datetime.strptime(date, "%Y-%m-%d %H:%M:%S")
                     .replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            ts = 0
        posts.append({
            "id": int(item.findtext("wp:post_id", namespaces=NS)),
            "slug": item.findtext("wp:post_name", namespaces=NS) or "",
            "title": item.findtext("title") or "(untitled)",
            "author": item.findtext("dc:creator", namespaces=NS) or "",
            "ts": ts,
            "tags": ",".join(c.text for c in item.findall("category")
                             if c.text),
            "html": item.findtext("content:encoded", namespaces=NS) or "",
        })
    posts.sort(key=lambda p: p["ts"])
    return blog_title, posts


def build_db(blog_title: str, posts: list[dict], out_path: str) -> None:
    con = sqlite3.connect(out_path)
    con.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE posts(id INTEGER PRIMARY KEY, slug TEXT, title TEXT,
                           author TEXT, ts INTEGER, tags TEXT, html TEXT);
        CREATE INDEX posts_ts ON posts(ts);
        CREATE INDEX posts_slug ON posts(slug);
        -- ts is IN the key: "newest posts matching <word>" walks one
        -- index range (see docs/roadmap.md v2 for the measured cost of
        -- getting this wrong)
        CREATE TABLE kw(word TEXT, ts INTEGER, id INTEGER,
                        PRIMARY KEY(word, ts, id)) WITHOUT ROWID;
    """)
    con.execute("INSERT INTO meta VALUES('title', ?)", (blog_title,))
    for p in posts:
        con.execute(
            "INSERT INTO posts VALUES(:id, :slug, :title, :author, :ts, :tags, :html)", p)
        text = f"{p['title']} {p['tags'].replace(',', ' ')} " + TAGS.sub(" ", p["html"])
        words = set(WORD.findall(text.lower()))
        con.executemany("INSERT OR IGNORE INTO kw VALUES(?, ?, ?)",
                        ((w, p["ts"], p["id"]) for w in words))
    con.commit()
    con.close()


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    blog_title, posts = parse_wxr(sys.argv[1])
    build_db(blog_title, posts, sys.argv[2])
    print(f"{len(posts)} posts from '{blog_title}' -> {sys.argv[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
