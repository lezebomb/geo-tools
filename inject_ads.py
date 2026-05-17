#!/usr/bin/env python3
"""Inject AdSense bootstrap infrastructure across all static index.html files."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PUBLISHER_ID = "pub-XXXXXXXXXXXXXXXX"
ADSENSE_CLIENT = "ca-pub-XXXXXXXXXXXXXXXX"
ADS_TXT_LINE = f"google.com, {PUBLISHER_ID}, DIRECT, f08c47fec0942fa0\n"
ADSENSE_SCRIPT = (
    f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={ADSENSE_CLIENT}" '
    'crossorigin="anonymous"></script>'
)
ADSENSE_SCRIPT_RE = re.compile(
    r'\s*<script\b(?=[^>]*\bsrc=["\']https://pagead2\.googlesyndication\.com/pagead/js/adsbygoogle\.js\?client=[^"\']+["\'])[^>]*>\s*</script>',
    re.IGNORECASE,
)
HEAD_RE = re.compile(r"<head\b[^>]*>", re.IGNORECASE)


def discover_index_files() -> list[Path]:
    files = [path for path in ROOT.rglob("index.html") if ".git" not in path.parts]
    return sorted(files, key=lambda item: item.relative_to(ROOT).as_posix())


def inject_adsense_script(html: str) -> tuple[str, bool]:
    if ADSENSE_SCRIPT in html and len(ADSENSE_SCRIPT_RE.findall(html)) == 1:
        return html, False

    without_existing = ADSENSE_SCRIPT_RE.sub("", html)
    if ADSENSE_SCRIPT in without_existing:
        return without_existing, without_existing != html

    match = HEAD_RE.search(without_existing)
    if not match:
        raise ValueError("Missing <head> tag")

    insert_at = match.end()
    updated = without_existing[:insert_at] + "\n  " + ADSENSE_SCRIPT + without_existing[insert_at:]
    return updated, updated != html


def main() -> int:
    (ROOT / "ads.txt").write_text(ADS_TXT_LINE, encoding="utf-8", newline="\n")

    touched = 0
    scanned = 0
    for path in discover_index_files():
        scanned += 1
        original = path.read_text(encoding="utf-8")
        updated, changed = inject_adsense_script(original)
        if changed:
            path.write_text(updated, encoding="utf-8", newline="\n")
            touched += 1

    print("Generated ads.txt")
    print(f"Scanned {scanned} index.html files")
    print(f"Updated {touched} index.html files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
