#!/usr/bin/env python3
"""Write CrossPaste Community's self-hosted F-Droid landing page."""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "fdroid/config.yml"
TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CrossPaste Community F-Droid Repository</title>
<style>
:root {{ color-scheme: light dark; }}
body {{ margin: 0 auto; max-width: 44rem; padding: 3rem 1.25rem; font: 16px/1.6 system-ui,sans-serif; }}
h1 {{ font-size: 1.8rem; }}
pre {{ padding: 1rem; border-radius: .6rem; background: rgba(127,127,127,.15); white-space: pre-wrap; overflow-wrap: anywhere; }}
.cta {{ display: inline-block; padding: .7rem 1.1rem; border-radius: .5rem; background: #006c4c; color: white; font-weight: 650; text-decoration: none; }}
footer {{ margin-top: 3rem; opacity: .75; }}
</style>
</head>
<body>
<h1>CrossPaste Community F-Droid Repository</h1>
<p>This self-hosted repository serves the same signed CrossPaste Community APKs attached to GitHub releases.</p>
<p><a class="cta" href="{add_url}">Add repository to F-Droid</a></p>
<p>Manual repository address:</p><pre>{repo_url}</pre>
<p>Repository signing fingerprint (SHA-256):</p><pre>{fingerprint}</pre>
<p>This is an CrossPaste Community-operated binary repository, not a listing in F-Droid's official catalog.</p>
<footer><a href="https://github.com/colonelpanic8/crosspaste-android">Source and issues</a></footer>
</body>
</html>
"""


def main() -> int:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} FDROID_BUILD_DIR OUTPUT_HTML", file=sys.stderr)
        return 2
    match = re.search(
        r"^repo_url:\s*(\S+)\s*$",
        CONFIG.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        print("repo_url missing from fdroid/config.yml", file=sys.stderr)
        return 1
    repo_url = match.group(1)
    fingerprint_path = Path(sys.argv[1]) / "fingerprint.txt"
    fingerprint = fingerprint_path.read_text(encoding="utf-8").strip()
    formatted = " ".join(
        fingerprint[index : index + 2].upper() for index in range(0, len(fingerprint), 2)
    )
    add_url = f"{repo_url}?fingerprint={fingerprint}"
    output = Path(sys.argv[2])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        TEMPLATE.format(
            add_url=html.escape(add_url, quote=True),
            repo_url=html.escape(repo_url),
            fingerprint=html.escape(formatted),
        ),
        encoding="utf-8",
    )
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
