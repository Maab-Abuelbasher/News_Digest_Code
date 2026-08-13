#!/usr/bin/env python3
"""
Network & Security News Digest
================================
Pulls the latest articles from a curated list of networking/security RSS
feeds, filters them by keyword relevance (tuned for Fortinet/F5 gear by
default), and outputs a digest -- to the console, a file, and/or Slack.

Usage:
    python3 security_digest.py                  # print digest to console
    python3 security_digest.py --hours 48        # look back 48 hours instead of 24
    python3 security_digest.py --out digest.md    # also save to a markdown file
    python3 security_digest.py --slack-webhook <url>   # also post to Slack
    python3 security_digest.py --summarize        # use Claude API to summarize (needs ANTHROPIC_API_KEY env var)

Schedule it with cron for a daily run, e.g. 7am every weekday:
    0 7 * * 1-5 /usr/bin/python3 /path/to/security_digest.py --out /path/to/digest.md --slack-webhook https://hooks.slack.com/...

Requirements:
    pip install feedparser requests
    pip install google-generative-ai   # only if using --summarize
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime


import feedparser

# ---------------------------------------------------------------------------
# CONFIG -- edit this section to add/remove sources or change keywords
# ---------------------------------------------------------------------------

FEEDS = {
    "The Hacker News":       "https://feeds.feedburner.com/TheHackersNews",
    "BleepingComputer":      "https://www.bleepingcomputer.com/feed/",
    "Krebs on Security":     "https://krebsonsecurity.com/feed/",
    "FortiGuard PSIRT":      "https://filestore.fortinet.com/fortiguard/rss/ir.xml",
    "r/networking":          "https://www.reddit.com/r/networking/.rss",
    "r/netsec":               "https://www.reddit.com/r/netsec/.rss",
    "Cisco Security Advisories":"https://tools.cisco.com/security/center/psirtrss20/CiscoSecurityAdvisory.xml",
}

# Keywords used to flag "high relevance" items (case-insensitive substring match).
# Tune this list to your own stack.
KEYWORDS = [
    "fortigate", "fortinet", "fortimanager", "forticlient", "fortios",
    "big-ip", "f5", "tmos", "f5os",
    "cve", "zero-day", "0-day", "exploit", "vulnerability", "patch",
    "firewall", "vpn", "load balancer", "rce", "critical", "network"
]

# ---------------------------------------------------------------------------


def parse_entry_date(entry):
    """Best-effort extraction of a publish datetime from a feed entry."""
    for field in ("published", "updated", "pubDate"):
        val = entry.get(field)
        if val:
            try:
                dt = parsedate_to_datetime(val)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except (TypeError, ValueError):
                pass
    if entry.get("published_parsed"):
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return None


def is_relevant(title, summary):
    text = f"{title} {summary}".lower()
    return any(kw in text for kw in KEYWORDS)


def fetch_digest(hours_back):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    results = []  # list of dicts: source, title, link, published, relevant

    for source, url in FEEDS.items():
        try:
            parsed = feedparser.parse(url)
        except Exception as e:
            print(f"[warn] failed to fetch {source}: {e}", file=sys.stderr)
            continue

        if parsed.bozo and not parsed.entries:
            print(f"[warn] could not parse feed for {source} ({url})", file=sys.stderr)
            continue

        for entry in parsed.entries:
            pub_dt = parse_entry_date(entry)
            if pub_dt is None or pub_dt < cutoff:
                continue

            title = entry.get("title", "(no title)")
            summary = entry.get("summary", "")
            link = entry.get("link", "")

            results.append({
                "source": source,
                "title": title,
                "link": link,
                "published": pub_dt,
                "relevant": is_relevant(title, summary),
            })

    # Sort: relevant items first, then newest first
    results.sort(key=lambda x: (not x["relevant"], -x["published"].timestamp()))
    return results


def build_markdown(results, hours_back):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Network & Security Digest", f"_Generated {now} — last {hours_back}h_\n"]

    high = [r for r in results if r["relevant"]]
    other = [r for r in results if not r["relevant"]]

    if high:
        lines.append("## 🔴 High relevance (matched your keywords)\n")
        for r in high:
            lines.append(f"- **[{r['title']}]({r['link']})** — {r['source']} ({r['published'].strftime('%b %d %H:%M UTC')})")
        lines.append("")

    if other:
        lines.append("## General\n")
        for r in other:
            lines.append(f"- [{r['title']}]({r['link']}) — {r['source']} ({r['published'].strftime('%b %d %H:%M UTC')})")

    if not results:
        lines.append("_No new items in this window._")

    return "\n".join(lines)


def summarize_with_Gemini(markdown_digest):
    """Optional: send the digest to Gemini for a short prioritized summary.
    Requires: pip install google-generativeai, and GEMINI_API_KEY set in the environment.
    """
    try:
        from google import genai
    except ImportError:
        print("[warn] google-generativeai package not installed; skipping --summarize. Run: pip install google-generativeai", file=sys.stderr)
        return None
 
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[warn] GEMINI_API_KEY not set; skipping --summarize.", file=sys.stderr)
        return None
 
    client = genai.Client()
    prompt = (
        "You are helping a network/security engineer who works hands-on with "
        "Fortinet (FortiGate, FortiManager) and F5 BIG-IP appliances. "
        "Below is a raw list of today's articles from security/networking RSS feeds. "
        "Write a short digest (max 10 bullet points) prioritizing anything that "
        "could require action on Fortinet or F5 gear (CVEs, patches, active exploits), "
        "then briefly note other notable industry news.\n\n"
        f"{markdown_digest}"
    )

    chat = client.chats.create(model="gemini-3.6-flash")
 
    resp = chat.send_message(prompt)

    #return "".join(block.text for block in resp.content if hasattr(block, "text"))
    return "".join(part.text for part in resp.candidates[0].content.parts if hasattr(part, "text") and part.text)
 

def post_to_slack(webhook_url, text):
    import requests
    resp = requests.post(webhook_url, json={"text": text})
    if resp.status_code != 200:
        print(f"[warn] Slack post failed: {resp.status_code} {resp.text}", file=sys.stderr)


def main():

    ap = argparse.ArgumentParser(description="Network & security news digest generator")
    ap.add_argument("--hours", type=int, default=24, help="Look-back window in hours (default: 24)")
    ap.add_argument("--out", type=str, help="Save digest to this markdown file")
    ap.add_argument("--slack-webhook", type=str, help="Post digest to this Slack incoming webhook URL")
    ap.add_argument("--summarize", action="store_true", help="Use Gemini API to generate a prioritized summary")
    args = ap.parse_args()

    results = fetch_digest(args.hours)
    digest_md = build_markdown(results, args.hours)

    output = digest_md
    if args.summarize:
        summary = summarize_with_Gemini(digest_md)
        if summary:
            output = f"{summary}\n\n---\n\n{digest_md}"
            print("[info] generated Gemini summary")
        else:
            print("[warn] summarization did not return text; sending full digest")

    print(output)

    if args.out:
        with open(args.out, "w") as f:
            f.write(output)

    if args.slack_webhook:
        # Slack has a payload size limit; keep it reasonably short
        post_to_slack(args.slack_webhook, output[:3800])
        print(f"[info] posted digest to Slack webhook {args.slack_webhook}")


if __name__ == "__main__":
    main()
