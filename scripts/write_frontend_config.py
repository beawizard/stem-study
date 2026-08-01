#!/usr/bin/env python3
"""Rewrite frontend/index.html STEM_CONFIG from CDK outputs or CLI args."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "frontend" / "index.html"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api-url", required=True)
    p.add_argument("--user-pool-id", required=True)
    p.add_argument("--user-pool-client-id", required=True)
    p.add_argument("--region", default="ap-southeast-1")
    p.add_argument(
        "--frontend-url",
        default="",
        help="Public SPA URL (e.g. https://stem.melon.com)",
    )
    p.add_argument(
        "--facebook-page-url",
        default="https://www.facebook.com/profile.php?id=61592589455670",
    )
    p.add_argument("--cache-bust", default="")
    args = p.parse_args()

    text = INDEX.read_text(encoding="utf-8")
    bust = args.cache_bust or __import__("datetime").datetime.utcnow().strftime(
        "%Y%m%d%H%M"
    )

    config_js = f"""window.STEM_CONFIG = {{
      apiUrl: {json.dumps(args.api_url.rstrip("/"))},
      userPoolId: {json.dumps(args.user_pool_id)},
      userPoolClientId: {json.dumps(args.user_pool_client_id)},
      region: {json.dumps(args.region)},
      frontendUrl: {json.dumps(args.frontend_url or "")},
      facebookPageUrl: {json.dumps(args.facebook_page_url)},
      gcashMerchant: "09XX-XXX-XXXX",
      monthlyPricePhp: 99
    }};"""

    text2, n = re.subn(
        r"window\.STEM_CONFIG\s*=\s*\{[\s\S]*?\};",
        config_js,
        text,
        count=1,
    )
    if n != 1:
        raise SystemExit("Could not find window.STEM_CONFIG block in index.html")

    # Cache-bust asset query strings
    text2 = re.sub(
        r"(href|src)=\"(css/styles\.css|js/api\.js|js/auth\.js|js/app\.js)\?v=[^\"]+\"",
        rf'\1="\2?v={bust}"',
        text2,
    )
    INDEX.write_text(text2, encoding="utf-8")
    print(f"Updated {INDEX} (cache bust {bust})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
