#!/usr/bin/env python3
"""Generate sitemap.xml and robots.txt for the static GEO tools array."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape


BASE_URL = "https://tools.lezebomb.top"
ROOT = Path(__file__).resolve().parent


def url_for_index(index_file: Path) -> str:
    relative = index_file.relative_to(ROOT).as_posix()
    if relative == "index.html":
        return f"{BASE_URL}/"
    return f"{BASE_URL}/{quote(relative, safe='/')}"


def lastmod_for(path: Path) -> str:
    timestamp = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc)
    return timestamp.date().isoformat()


def discover_index_files() -> list[Path]:
    files = [path for path in ROOT.rglob("index.html") if ".git" not in path.parts]
    return sorted(files, key=lambda item: item.relative_to(ROOT).as_posix())


def build_sitemap(index_files: list[Path]) -> str:
    entries = []
    for path in index_files:
        location = escape(url_for_index(path))
        priority = "1.0" if path.relative_to(ROOT).as_posix() == "index.html" else "0.8"
        entries.append(
            "  <url>\n"
            f"    <loc>{location}</loc>\n"
            f"    <lastmod>{lastmod_for(path)}</lastmod>\n"
            "    <changefreq>weekly</changefreq>\n"
            f"    <priority>{priority}</priority>\n"
            "  </url>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries)
        + "\n</urlset>\n"
    )


def build_robots() -> str:
    return (
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {BASE_URL}/sitemap.xml\n"
    )


def main() -> int:
    index_files = discover_index_files()
    if not index_files:
        raise SystemExit("No index.html files found; sitemap generation aborted.")
    (ROOT / "sitemap.xml").write_text(build_sitemap(index_files), encoding="utf-8", newline="\n")
    (ROOT / "robots.txt").write_text(build_robots(), encoding="utf-8", newline="\n")
    print(f"Generated sitemap.xml with {len(index_files)} URLs")
    print("Generated robots.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
