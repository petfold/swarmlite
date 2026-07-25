"""Generate a deterministic sample WXR file — a small fictional blog —
so the WordPress demo runs without needing anyone's real export.

    python make_sample_wxr.py sample.wxr
"""

import sys
from xml.sax.saxutils import escape

TOPICS = [
    ("solar", "Notes on the balcony panel", "renewables,diy"),
    ("swarm", "Hosting this blog on Swarm", "web3,hosting"),
    ("garden", "The tomatoes forgave us", "garden,summer"),
    ("bees", "A season with the city bees", "bees,urban"),
    ("repair", "Fixing the kettle, again", "repair,right-to-repair"),
    ("bread", "Sourdough for impatient people", "kitchen,bread"),
    ("bike", "Cargo bike conversion diary", "transport,diy"),
    ("water", "Rainwater math for one roof", "water,numbers"),
]

PARA = (
    "<p>That was the week we learned that {t} projects never go to plan, "
    "and that the plan was the least interesting part anyway. Some sketches "
    "and numbers below.</p><p>Long-form body text so search has something "
    "to chew on: verifiable, resilient, neighbourly {t} — the kind you can "
    "publish once and not babysit. <em>No servers were harmed.</em></p>"
)


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "sample.wxr"
    items = []
    for i in range(48):
        key, title, tags = TOPICS[i % len(TOPICS)]
        n = i // len(TOPICS) + 1
        day = i % 28 + 1
        month = i % 12 + 1
        cats = "".join(
            f"<category domain=\"post_tag\" nicename=\"{c}\"><![CDATA[{c}]]></category>"
            for c in tags.split(","))
        items.append(f"""
    <item>
      <title>{escape(title)} #{n}</title>
      <dc:creator><![CDATA[peter]]></dc:creator>
      {cats}
      <wp:post_id>{i + 1}</wp:post_id>
      <wp:post_date_gmt>2025-{month:02d}-{day:02d} 09:{i % 60:02d}:00</wp:post_date_gmt>
      <wp:post_name>{key}-{n}</wp:post_name>
      <wp:post_type>post</wp:post_type>
      <wp:status>publish</wp:status>
      <content:encoded><![CDATA[<h2>{escape(title)} #{n}</h2>{PARA.format(t=key)}]]></content:encoded>
    </item>""")
    # one page and one draft, which the exporter must skip
    items.append("""
    <item>
      <title>About</title>
      <wp:post_id>900</wp:post_id>
      <wp:post_type>page</wp:post_type>
      <wp:status>publish</wp:status>
    </item>
    <item>
      <title>Unfinished thought</title>
      <wp:post_id>901</wp:post_id>
      <wp:post_type>post</wp:post_type>
      <wp:status>draft</wp:status>
    </item>""")

    open(out, "w").write(f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:wp="http://wordpress.org/export/1.2/">
  <channel>
    <title>Solarpunk Notebook</title>
    <wp:wxr_version>1.2</wp:wxr_version>{''.join(items)}
  </channel>
</rss>
""")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
