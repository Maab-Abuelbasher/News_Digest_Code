# News_Digest_Code
Python Code to pulls the latest articles from a curated list of networking/security RSSfeeds, filters them by keyword relevance and outputs a digest -- to the console, a file, and/or Slack.

Usage:
    python3 security_digest.py                  # print digest to console
    python3 security_digest.py --hours 48        # look back 48 hours instead of 24
    python3 security_digest.py --out digest.md    # also save to a markdown file
    python3 security_digest.py --slack-webhook <url>   # also post to Slack
    python3 security_digest.py --summarize        # use Claude API to summarize (needs ANTHROPIC_API_KEY env var)
