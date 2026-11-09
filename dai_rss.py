#!/usr/bin/env python3
"""Unofficial RSS 2.0 feed for doubleAI research -- stdlib only, 1 HTTP call.

Source: https://www.doubleai.com/llms.txt (the site's own dated manifest).
Line format: - [Title](URL): description. — Type · Oct 25, 2026 · Topic, Topic
"""
from __future__ import annotations

import html, json, os, re, sys, time, urllib.error, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

SITE = "https://www.doubleai.com"
KEY = os.getenv("TINYFISH_API_KEY", "").strip()
calls = 0
esc = html.escape
redact = lambda s: s.replace(KEY, "[redacted]") if KEY and KEY in s else s


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a):
        raise urllib.error.HTTPError(a[0].full_url, a[2], "redirect blocked", a[3], a[1])


def tinyfish(url: str) -> str:
    global calls
    if calls >= 4:
        raise RuntimeError("tinyfish call cap (4) reached")
    calls += 1
    req = urllib.request.Request(
        "https://api.fetch.tinyfish.ai",
        json.dumps({"urls": [url], "format": "markdown"}).encode(),
        {"X-API-Key": KEY, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=60) as r:  # nosemgrep
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"tinyfish http {e.code} for {url}") from None
    if not (res := out.get("results")) or not res[0].get("text"):
        raise RuntimeError(f"tinyfish failed: {redact(str(out.get('errors')))}")
    return res[0]["text"]


def fetch(url: str) -> str:
    if not url.startswith(f"{SITE}/"):
        raise ValueError(f"refusing non-site url: {url}")
    err: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dai-rss/1.0", "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=25) as r:  # nosemgrep
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            err = e
            if e.code != 429 or attempt == 2:
                break
            time.sleep(min(float(e.headers.get("Retry-After") or 2), 10))
        except (urllib.error.URLError, TimeoutError) as e:
            err = e
            time.sleep(2)
    if KEY:
        print(f"[warn] {url} failed ({redact(str(err))}); via TinyFish", file=sys.stderr)
        return tinyfish(url)
    raise err  # type: ignore[misc]


def items() -> list[dict]:
    txt = fetch(f"{SITE}/llms.txt")
    # ponytail: Parse the Research section only (up to ## News) with one regex; full Markdown parsing is overkill for a flat list.
    research = txt.split("## Research", 1)[-1].split("## News", 1)[0]
    out, seen = [], set()
    for m in re.finditer(r"^- \[(.+?)\]\((.+?)\): (.*?) — (.+?) · (.+?) · (.+)$", research, re.M):
        title, link, desc, kind, date, topics = m.groups()
        if link in seen:
            continue
        seen.add(link)
        try:
            pub = datetime.strptime(date, "%b %d, %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            pub = None
        tags = [kind, *[t.strip() for t in topics.split(",") if t.strip()]]
        out.append({
            "title": title,
            "link": link,
            "pub": pub,
            "html": f"<p>{esc(desc)}</p><p><i>{esc(kind)} · {esc(topics)}</i></p>",
            "tags": tags[:6],
            "source": "doubleAI",
            "creator": "doubleAI Team",
        })
    return out


def rss(title: str, link: str, desc: str, rows: list[dict], self_url: str | None) -> str:
    stamp = max((r["pub"] for r in rows if r["pub"]), default=datetime.now(timezone.utc))
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/elements/1.1/">',
        f"<channel><title>{esc(title)}</title><link>{esc(link)}</link><description>{esc(desc)}</description>",
        f"<language>en</language><lastBuildDate>{format_datetime(stamp)}</lastBuildDate><ttl>10</ttl>",
    ]
    if self_url:
        out.append(f'<atom:link href="{esc(self_url)}" rel="self" type="application/rss+xml"/>')
    for r in rows:
        out.append(f'<item><title>{esc(r["title"])}</title><link>{esc(r["link"])}</link><guid isPermaLink="true">{esc(r["link"])}</guid>')
        if r["pub"]:
            out.append(f"<pubDate>{format_datetime(r['pub'])}</pubDate>")
        if r.get("creator"):
            out.append(f"<dc:creator>{esc(r['creator'])}</dc:creator>")
        if r["html"]:
            out.append(f"<description>{esc(r['html'])}</description>")
        if r.get("source"):
            out.append(f'<source url="{esc(r["link"])}">{esc(r["source"])}</source>')
        out += [f"<category>{esc(t)}</category>" for t in r.get("tags", ())] + ["</item>"]
    return "\n".join(out + ["</channel>", "</rss>", ""])


def main() -> int:
    out = Path(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else ".")
    base = (sys.argv[sys.argv.index("--base-url") + 1] if "--base-url" in sys.argv else os.getenv("FEED_BASE_URL", "")).rstrip("/")
    feed = rss("doubleAI — Research", f"{SITE}/research", "Papers, results and talks from doubleAI on expert-level AI.", items(), f"{base}/doubleai.xml" if base else None)
    if "<item>" not in feed:
        raise SystemExit("error: upstream returned no items; refusing to write an empty feed")
    ET.fromstring(feed)
    feed = redact(feed)
    if KEY and KEY in feed:
        raise SystemExit("error: doubleai.xml would contain the API key")
    out.mkdir(parents=True, exist_ok=True)
    p = out / "doubleai.xml"
    if not p.exists() or p.read_bytes() != feed.encode():
        p.write_bytes(feed.encode())
        print("doubleai.xml updated")
    else:
        print("doubleai.xml unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
