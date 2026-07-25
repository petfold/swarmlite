"""WordPress exporter: WXR parsing, database shape, and the read
budget of the queries the theme runs — all offline over memory://."""

import subprocess
import sys
from pathlib import Path

import fsspec
import pytest

import swarmlite

WP = Path(__file__).parent.parent / "examples" / "wordpress"
sys.path.insert(0, str(WP))

from wp_export import build_db, parse_wxr  # noqa: E402


@pytest.fixture(scope="module")
def blog_url(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("wp")
    wxr = tmp / "sample.wxr"
    subprocess.run(
        [sys.executable, str(WP / "make_sample_wxr.py"), str(wxr)],
        check=True, capture_output=True)
    title, posts = parse_wxr(str(wxr))
    assert title == "Solarpunk Notebook"
    db = tmp / "blog.db"
    build_db(title, posts, str(db))
    fsspec.filesystem("memory").pipe_file("/blog.db", db.read_bytes())
    return "memory://blog.db"


def test_pages_and_drafts_are_skipped(blog_url):
    con = swarmlite.connect(blog_url)
    (n,) = next(iter(con.execute("SELECT count(*) FROM posts")))
    assert n == 48  # 48 posts; the page and the draft were skipped
    titles = [t for (t,) in con.execute("SELECT title FROM posts")]
    assert "About" not in titles and "Unfinished thought" not in titles


def test_theme_queries_and_read_budget(blog_url):
    con = swarmlite.connect(blog_url)
    f = con.swarmlite_file

    # index page: latest 20
    rows = list(con.execute(
        "SELECT slug, title FROM posts ORDER BY ts DESC LIMIT 20"))
    assert len(rows) == 20

    # search: body words are indexed, newest first, via the covering kw key
    before = f.stats()["pages_fetched"]
    hits = list(con.execute(
        "SELECT p.title FROM kw JOIN posts p ON p.id = kw.id "
        "WHERE kw.word = 'verifiable' ORDER BY kw.ts DESC LIMIT 20"))
    assert len(hits) == 20
    assert f.stats()["pages_fetched"] - before < 40

    # a post page: slug lookup returns the full HTML
    (html,) = next(iter(con.execute(
        "SELECT html FROM posts WHERE slug = 'swarm-2'")))
    assert "<h2>" in html and "Swarm" in html
