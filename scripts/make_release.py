"""Build a clean release zip of the project (source + static site + Worker + docs) and prepend a
CHANGELOG entry listing files changed since the previous release.

    .venv/Scripts/python scripts/make_release.py ["short note for the changelog"]
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import __version__  # noqa: E402

INCLUDE_DIRS = ["app", "static", "cloudflare", "scripts", "tests", "samples"]
INCLUDE_FILES = ["README.md", "DEPLOY.md", "CHANGELOG.md", "requirements.txt", ".env.example", ".gitignore", "run.bat"]
SKIP_PARTS = {"__pycache__", ".pytest_cache", "node_modules"}
SKIP_SUFFIX = {".pyc", ".log"}
STATE = ROOT / "dist" / ".release-state.json"


def iter_files():
    for d in INCLUDE_DIRS:
        for p in sorted((ROOT / d).rglob("*")):
            if p.is_file() and not (SKIP_PARTS & set(p.parts)) and p.suffix not in SKIP_SUFFIX:
                yield p
    for f in INCLUDE_FILES:
        p = ROOT / f
        if p.exists():
            yield p


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def main() -> None:
    note = " ".join(sys.argv[1:]).strip()
    (ROOT / "dist").mkdir(exist_ok=True)
    prev = json.loads(STATE.read_text()) if STATE.exists() else {}
    files = list(iter_files())
    cur = {str(p.relative_to(ROOT)).replace("\\", "/"): digest(p) for p in files if p.name != "CHANGELOG.md"}
    added = sorted(k for k in cur if k not in prev)
    changed = sorted(k for k in cur if k in prev and prev[k] != cur[k])
    removed = sorted(k for k in prev if k not in cur)

    stamp = time.strftime("%Y-%m-%d")
    lines = [f"## {__version__} - {stamp}", ""]
    if note:
        lines += [note, ""]
    for label, items in (("Added", added), ("Changed", changed), ("Removed", removed)):
        if items:
            lines.append(f"### {label}")
            lines += [f"- {i}" for i in items[:80]]
            if len(items) > 80:
                lines.append(f"- ... and {len(items) - 80} more")
            lines.append("")
    if not (added or changed or removed) and not note:
        lines += ["No file changes since the previous release.", ""]
    changelog = ROOT / "CHANGELOG.md"
    old = changelog.read_text(encoding="utf-8") if changelog.exists() else "# Changelog\n\n"
    head, _, rest = old.partition("\n\n")
    changelog.write_text(head + "\n\n" + "\n".join(lines) + "\n" + rest, encoding="utf-8")

    out = ROOT / "dist" / f"burstai-{__version__}-{time.strftime('%Y%m%d-%H%M')}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in iter_files():
            z.write(p, f"burstai/{p.relative_to(ROOT).as_posix()}")
    STATE.write_text(json.dumps(cur, indent=0))
    print(f"wrote {out} ({out.stat().st_size // 1024} KB) - {len(added)} added, {len(changed)} changed, {len(removed)} removed")


if __name__ == "__main__":
    main()
