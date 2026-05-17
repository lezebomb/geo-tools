#!/usr/bin/env python3
"""Finalize production rollout for the static GEO tools array.

This script is intentionally idempotent:
- aligns every canonical, og:url, manifest URL, and JSON-LD URL to tools.lezebomb.top
- injects runtime resilience guards once
- keeps AdSense placeholders unless --pub-id is provided
- reruns generate_seo.py so sitemap.xml tracks the final routing
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote


ROOT = Path(__file__).resolve().parent
BASE_URL = "https://tools.lezebomb.top"
DUMMY_DOMAIN = "yourmaindomain.top"
PRODUCTION_DOMAIN = "tools.lezebomb.top"

ERROR_BOUNDARY_SCRIPT = """  <script id="global-error-boundary">
    (function () {
      "use strict";
      var hasShownNotice = false;
      function showRuntimeNotice(message) {
        if (hasShownNotice) return;
        hasShownNotice = true;
        var render = function () {
          if (document.getElementById("runtime-error-notice")) return;
          var notice = document.createElement("div");
          notice.id = "runtime-error-notice";
          notice.setAttribute("role", "status");
          notice.style.cssText = "position:fixed;left:16px;right:16px;bottom:16px;z-index:9999;padding:12px 14px;border:1px solid rgba(251,113,133,.45);border-radius:8px;background:rgba(15,23,42,.96);color:#ffe4e6;font:600 13px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;box-shadow:0 18px 60px rgba(0,0,0,.32)";
          notice.textContent = message || "A runtime module recovered from an error. Reload if this panel stays visible.";
          document.body.appendChild(notice);
        };
        if (document.body) render();
        else document.addEventListener("DOMContentLoaded", render, { once: true });
      }
      window.onerror = function (message, source, line, column, error) {
        console.warn("Recovered runtime error:", message, source, line, column, error);
        showRuntimeNotice("A runtime module recovered from an error. The page is still available.");
        return false;
      };
      window.addEventListener("unhandledrejection", function (event) {
        console.warn("Recovered unhandled promise rejection:", event.reason);
        showRuntimeNotice("A background task recovered from an error. The page is still available.");
      });
    })();
  </script>"""

PASSIVE_LISTENER_GUARD = """  <script id="passive-listener-guard">
    (function () {
      "use strict";
      var passiveEvents = { scroll: true, wheel: true, touchstart: true, touchmove: true };
      var nativeAddEventListener = EventTarget.prototype.addEventListener;
      EventTarget.prototype.addEventListener = function (type, listener, options) {
        if (passiveEvents[type]) {
          if (options === undefined) {
            options = { passive: true };
          } else if (typeof options === "boolean") {
            options = { capture: options, passive: true };
          } else if (options && typeof options === "object" && options.passive === undefined) {
            options = Object.assign({}, options, { passive: true });
          }
        }
        return nativeAddEventListener.call(this, type, listener, options);
      };
    })();
  </script>"""

ADSENSE_PLACEHOLDERS = (
    ("pub-XXXXXXXXXXXXXXXX", "pub-{pub_id}"),
    ("ca-pub-XXXXXXXXXXXXXXXX", "ca-pub-{pub_id}"),
    ("pub-xxxxxxxxxxxxxxxx", "pub-{pub_id}"),
    ("ca-pub-xxxxxxxxxxxxxxxx", "ca-pub-{pub_id}"),
)


def discover_index_files() -> list[Path]:
    return sorted(
        [path for path in ROOT.rglob("index.html") if ".git" not in path.parts],
        key=lambda item: item.relative_to(ROOT).as_posix(),
    )


def discover_manifest_files() -> list[Path]:
    return sorted(
        [path for path in ROOT.rglob("pipeline-manifest.json") if ".git" not in path.parts],
        key=lambda item: item.relative_to(ROOT).as_posix(),
    )


def route_for_index(index_file: Path) -> str:
    relative = index_file.relative_to(ROOT).as_posix()
    if relative == "index.html":
        return f"{BASE_URL}/"
    return f"{BASE_URL}/{quote(relative, safe='/')}"


def route_for_manifest(manifest_file: Path) -> str:
    slug = manifest_file.parent.name
    return f"{BASE_URL}/{quote(slug, safe='')}/index.html"


def title_from_html(html_text: str, fallback: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return fallback
    return re.sub(r"\s+", " ", match.group(1)).strip()


def description_from_html(html_text: str, fallback: str) -> str:
    match = re.search(
        r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']\s*/?>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return fallback
    return re.sub(r"\s+", " ", match.group(1)).strip()


def upsert_canonical(html_text: str, route: str) -> str:
    canonical = f'  <link rel="canonical" href="{route}" />'
    pattern = re.compile(r'\s*<link\s+rel=["\']canonical["\'][^>]*>', re.IGNORECASE)
    if pattern.search(html_text):
        return pattern.sub("\n" + canonical, html_text, count=1)
    return insert_after_head_meta(html_text, canonical)


def upsert_og_url(html_text: str, route: str) -> str:
    tag = f'  <meta property="og:url" content="{route}" />'
    pattern = re.compile(r'\s*<meta\s+property=["\']og:url["\'][^>]*>', re.IGNORECASE)
    if pattern.search(html_text):
        return pattern.sub("\n" + tag, html_text, count=1)
    return insert_after_head_meta(html_text, tag)


def insert_after_head_meta(html_text: str, line: str) -> str:
    robots_match = re.search(r'<meta\s+name=["\']robots["\'][^>]*>\s*', html_text, flags=re.IGNORECASE)
    if robots_match:
        return html_text[: robots_match.end()] + line + "\n" + html_text[robots_match.end():]
    head_match = re.search(r"<head\b[^>]*>", html_text, flags=re.IGNORECASE)
    if not head_match:
        raise ValueError("Missing <head> tag")
    return html_text[: head_match.end()] + "\n" + line + html_text[head_match.end():]


def remove_script_by_id(html_text: str, script_id: str) -> str:
    pattern = re.compile(
        rf'\s*<script\b(?=[^>]*\bid=["\']{re.escape(script_id)}["\'])[^>]*>.*?</script>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    return pattern.sub("", html_text)


def inject_head_script(html_text: str, script_block: str) -> str:
    html_text = remove_script_by_id(html_text, re.search(r'id="([^"]+)"', script_block).group(1))
    head_match = re.search(r"<head\b[^>]*>", html_text, flags=re.IGNORECASE)
    if not head_match:
        raise ValueError("Missing <head> tag")
    return html_text[: head_match.end()] + "\n" + script_block + html_text[head_match.end():]


def patch_simple_passive_listener_calls(html_text: str) -> str:
    events = "scroll|wheel|touchstart|touchmove"
    pattern = re.compile(
        rf"addEventListener\(\s*(['\"])(?P<event>{events})\1\s*,\s*(?P<handler>[A-Za-z_$][\w$\.]*)\s*\)",
        flags=re.IGNORECASE,
    )
    return pattern.sub(
        lambda match: f"addEventListener({match.group(1)}{match.group('event')}{match.group(1)}, {match.group('handler')}, {{ passive: true }})",
        html_text,
    )


def align_subdomain_url_artifacts(text: str, route: str) -> str:
    text = text.replace(DUMMY_DOMAIN, PRODUCTION_DOMAIN)
    text = re.sub(r"https://[a-z0-9-]+\.tools\.lezebomb\.top/?", route, text)
    return text


def update_jsonld_value(value: Any, route: str) -> Any:
    if isinstance(value, dict):
        updated: dict[str, Any] = {}
        for key, nested in value.items():
            if key in {"@id", "url"}:
                updated[key] = route
            else:
                updated[key] = update_jsonld_value(nested, route)
        if "@type" in updated and isinstance(updated.get("@type"), str) and updated["@type"] in {
            "WebApplication",
            "SoftwareApplication",
            "WebPage",
            "FAQPage",
        }:
            updated["@id"] = route
            updated["url"] = route
        return updated
    if isinstance(value, list):
        return [update_jsonld_value(item, route) for item in value]
    if isinstance(value, str):
        return align_subdomain_url_artifacts(value, route)
    return value


def root_schema(index_files: list[Path], html_text: str) -> dict[str, Any]:
    items = []
    position = 1
    for index_file in index_files:
        if index_file.relative_to(ROOT).as_posix() == "index.html":
            continue
        tool_html = index_file.read_text(encoding="utf-8")
        items.append(
            {
                "@type": "ListItem",
                "position": position,
                "name": title_from_html(tool_html, index_file.parent.name),
                "url": route_for_index(index_file),
            }
        )
        position += 1
    return {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "WebSite",
                "@id": f"{BASE_URL}/",
                "url": f"{BASE_URL}/",
                "name": title_from_html(html_text, "GEO Tools Array"),
                "description": description_from_html(html_text, "A static array of privacy-first client-side utilities."),
            },
            {
                "@type": "ItemList",
                "@id": f"{BASE_URL}/",
                "url": f"{BASE_URL}/",
                "name": "GEO Tools Array",
                "itemListElement": items,
            },
        ],
    }


def upsert_jsonld(html_text: str, route: str, index_file: Path, all_index_files: list[Path]) -> str:
    pattern = re.compile(
        r'<script\s+type=["\']application/ld\+json["\']>\s*(.*?)\s*</script>',
        flags=re.IGNORECASE | re.DOTALL,
    )

    if index_file.relative_to(ROOT).as_posix() == "index.html":
        schema = root_schema(all_index_files, html_text)
    else:
        match = pattern.search(html_text)
        if match:
            try:
                parsed = json.loads(match.group(1))
                schema = update_jsonld_value(parsed, route)
            except json.JSONDecodeError:
                schema = fallback_tool_schema(html_text, route)
        else:
            schema = fallback_tool_schema(html_text, route)

    block = '<script type="application/ld+json">\n' + json.dumps(schema, ensure_ascii=False, indent=2) + "\n  </script>"
    if pattern.search(html_text):
        return pattern.sub(block, html_text, count=1)
    return insert_before_first_head_script_or_style(html_text, "  " + block)


def fallback_tool_schema(html_text: str, route: str) -> dict[str, Any]:
    name = title_from_html(html_text, "Client-Side Utility")
    description = description_from_html(html_text, "A privacy-first browser utility.")
    return {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "WebApplication",
                "@id": route,
                "url": route,
                "name": name,
                "description": description,
                "applicationCategory": "UtilityApplication",
                "operatingSystem": "All",
                "browserRequirements": "Requires JavaScript. Requires HTML5.",
                "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
            }
        ],
    }


def insert_before_first_head_script_or_style(html_text: str, block: str) -> str:
    match = re.search(r"\s*(?:<script\b|<style\b)", html_text, flags=re.IGNORECASE)
    head_match = re.search(r"<head\b[^>]*>", html_text, flags=re.IGNORECASE)
    if match and head_match and match.start() > head_match.end():
        return html_text[: match.start()] + "\n" + block + "\n" + html_text[match.start():]
    if not head_match:
        raise ValueError("Missing <head> tag")
    return html_text[: head_match.end()] + "\n" + block + html_text[head_match.end():]


def apply_pub_id(text: str, pub_id: str | None) -> str:
    if not pub_id:
        return text
    for placeholder, template in ADSENSE_PLACEHOLDERS:
        text = text.replace(placeholder, template.format(pub_id=pub_id))
    text = text.replace("XXXXXXXXXXXXXXXX", pub_id)
    text = text.replace("xxxxxxxxxxxxxxxx", pub_id)
    return text


def normalize_pub_id(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = raw.strip()
    cleaned = cleaned.removeprefix("ca-pub-").removeprefix("pub-")
    if not re.fullmatch(r"\d{12,20}", cleaned):
        raise SystemExit("--pub-id must be the numeric AdSense publisher id, for example 1234567890123456.")
    return cleaned


def process_index_file(path: Path, all_index_files: list[Path], pub_id: str | None) -> bool:
    original = path.read_text(encoding="utf-8")
    route = route_for_index(path)
    updated = align_subdomain_url_artifacts(original, route)
    updated = upsert_canonical(updated, route)
    updated = upsert_og_url(updated, route)
    updated = upsert_jsonld(updated, route, path, all_index_files)
    updated = patch_simple_passive_listener_calls(updated)
    updated = inject_head_script(updated, ERROR_BOUNDARY_SCRIPT)
    updated = inject_head_script(updated, PASSIVE_LISTENER_GUARD)
    updated = apply_pub_id(updated, pub_id)
    if updated != original:
        path.write_text(updated, encoding="utf-8", newline="\n")
        return True
    return False


def process_manifest_file(path: Path, pub_id: str | None) -> bool:
    original = path.read_text(encoding="utf-8")
    route = route_for_manifest(path)
    updated = align_subdomain_url_artifacts(original, route)
    updated = re.sub(r'"url"\s*:\s*"[^"]*"', f'"url": "{route}"', updated, count=1)
    updated = re.sub(r'"generated_url"\s*:\s*"[^"]*"', f'"generated_url": "{route}"', updated)
    updated = apply_pub_id(updated, pub_id)
    if updated != original:
        path.write_text(updated, encoding="utf-8", newline="\n")
        return True
    return False


def process_ads_txt(pub_id: str | None) -> bool:
    path = ROOT / "ads.txt"
    if not path.exists():
        path.write_text("google.com, pub-XXXXXXXXXXXXXXXX, DIRECT, f08c47fec0942fa0\n", encoding="utf-8", newline="\n")
        original = ""
    else:
        original = path.read_text(encoding="utf-8")
    updated = apply_pub_id(original, pub_id)
    if updated != original:
        path.write_text(updated, encoding="utf-8", newline="\n")
        return True
    return False


def rerun_generate_seo() -> None:
    script = ROOT / "generate_seo.py"
    if not script.exists():
        raise SystemExit("generate_seo.py is missing; cannot refresh sitemap.xml and robots.txt.")
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(result.returncode)


def validate_rollout() -> None:
    failures: list[str] = []
    for path in discover_index_files():
        text = path.read_text(encoding="utf-8")
        route = route_for_index(path)
        if DUMMY_DOMAIN in text:
            failures.append(f"{path.relative_to(ROOT)} still contains {DUMMY_DOMAIN}")
        if f'<link rel="canonical" href="{route}"' not in text:
            failures.append(f"{path.relative_to(ROOT)} has incorrect canonical")
        if f'<meta property="og:url" content="{route}"' not in text:
            failures.append(f"{path.relative_to(ROOT)} has incorrect og:url")
        if 'id="global-error-boundary"' not in text:
            failures.append(f"{path.relative_to(ROOT)} is missing global error boundary")
        if 'id="passive-listener-guard"' not in text:
            failures.append(f"{path.relative_to(ROOT)} is missing passive listener guard")
    for path in discover_manifest_files():
        text = path.read_text(encoding="utf-8")
        if DUMMY_DOMAIN in text:
            failures.append(f"{path.relative_to(ROOT)} still contains {DUMMY_DOMAIN}")
    if failures:
        raise SystemExit("Rollout validation failed:\n" + "\n".join(failures))


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize production rollout for tools.lezebomb.top.")
    parser.add_argument("--pub-id", help="Numeric Google AdSense publisher id, for example 1234567890123456.")
    args = parser.parse_args()
    pub_id = normalize_pub_id(args.pub_id)

    index_files = discover_index_files()
    manifest_files = discover_manifest_files()
    if not index_files:
        raise SystemExit("No index.html files found.")

    changed_indexes = sum(1 for path in index_files if process_index_file(path, index_files, pub_id))
    changed_manifests = sum(1 for path in manifest_files if process_manifest_file(path, pub_id))
    ads_changed = process_ads_txt(pub_id)
    rerun_generate_seo()
    validate_rollout()

    print(f"Processed {len(index_files)} index.html files; changed {changed_indexes}.")
    print(f"Processed {len(manifest_files)} pipeline-manifest.json files; changed {changed_manifests}.")
    print(f"ads.txt changed: {str(ads_changed).lower()}.")
    print("Publisher ID mode: " + (f"real id applied ({pub_id})" if pub_id else "placeholders preserved"))
    print("Production rollout prep complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
