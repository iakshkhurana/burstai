"""Download the two UI fonts (Lexend, Bricolage Grotesque - both SIL OFL) from Google Fonts once and
write them to static/fonts/ with a local stylesheet, so the site makes no third-party font requests.

    .venv/Scripts/python scripts/fetch_fonts.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "fonts"
CSS_URL = (
    "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500..800"
    "&family=Lexend:wght@300..600&display=swap"
)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
KEEP = {"latin", "latin-ext"}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers={"User-Agent": UA}, timeout=60, follow_redirects=True) as c:
        css = c.get(CSS_URL).text
        blocks = re.findall(r"/\*\s*([\w-]+)\s*\*/\s*(@font-face\s*\{[^}]+\})", css)
        if not blocks:
            print("no @font-face blocks found", file=sys.stderr)
            return 1
        out_css = ["/* Self-hosted copies of Lexend and Bricolage Grotesque (SIL Open Font License 1.1). */"]
        n = 0
        for subset, block in blocks:
            if subset not in KEEP:
                continue
            fam = re.search(r"font-family:\s*'([^']+)'", block).group(1)
            url = re.search(r"url\((https://[^)]+\.woff2)\)", block).group(1)
            name = f"{fam.lower().replace(' ', '-')}-{subset}.woff2"
            data = None
            for _ in range(5):
                try:
                    data = c.get(url).content
                    break
                except Exception as e:  # noqa: BLE001
                    print("retry", name, type(e).__name__)
            if not data:
                print("failed", name, file=sys.stderr)
                return 1
            (OUT / name).write_bytes(data)
            out_css.append(block.replace(url, f"/static/fonts/{name}"))
            n += 1
            print(f"{name}: {len(data) // 1024} KB")
        (ROOT / "static" / "css" / "fonts.css").write_text("\n".join(out_css) + "\n", encoding="utf-8")
        print(f"wrote {n} fonts + static/css/fonts.css")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
