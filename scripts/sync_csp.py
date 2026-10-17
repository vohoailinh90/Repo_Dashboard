#!/usr/bin/env python3
"""Keep the Content-Security-Policy of index.html in step with the page: the policy allows exactly one script and one style, by hash.

    python3 scripts/sync_csp.py           rewrite the two hashes
    python3 scripts/sync_csp.py --check   exit 1 and say so if they differ (the test suite runs this)

Edit the script or the style of the page without running this and the browser refuses to run the page, which is the point of a hash policy.
"""

from __future__ import annotations

import base64
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "index.html"

SCRIPT_RE = re.compile(r"<script>(.*?)</script>", re.S)
STYLE_RE = re.compile(r"<style>(.*?)</style>", re.S)


def sha256(text: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode()


def synced(html: str) -> str:
    """The page with both hashes brought up to date."""
    script, style = SCRIPT_RE.search(html), STYLE_RE.search(html)
    if not script or not style:
        raise ValueError("the page needs exactly one <script> and one <style> block")
    html = re.sub(r"script-src 'sha256-[^']*'", f"script-src '{sha256(script.group(1))}'", html, count=1)
    return re.sub(r"style-src 'sha256-[^']*'", f"style-src '{sha256(style.group(1))}'", html, count=1)


def main(argv: list[str]) -> int:
    html = PAGE.read_text(encoding="utf-8")
    fresh = synced(html)
    if "--check" in argv:
        if fresh != html:
            print("index.html is out of step (its script or style changed without the CSP hashes): run python3 scripts/sync_csp.py")
            return 1
        print("index.html is in step")
        return 0
    PAGE.write_text(fresh, encoding="utf-8")
    print("index.html updated" if fresh != html else "index.html already in step")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
