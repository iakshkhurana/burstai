"""Print raw detector statistics for every file in samples/ so the calibration constants in
app/aidetect/engine.py can be tuned. Files named *human* are treated as human, others as AI.

    .venv/Scripts/python -m tests.calibrate
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.aidetect.engine import detect_ai  # noqa: E402
from app.aidetect.lm import SCORER  # noqa: E402


async def main() -> None:
    root = Path(__file__).resolve().parent.parent
    files = sorted((root / "samples").glob("*.txt")) + sorted((root / "static" / "samples").glob("*.txt"))
    print("loading models...", flush=True)
    SCORER.load()
    print(SCORER.info())
    print(f"{'file':34} {'label':6} {'bino':>7} {'logppl':>7} {'burst':>6} {'style':>6} {'p(AI)':>6}  verdict")
    correct = total = 0
    for f in files:
        text = f.read_text(encoding="utf-8", errors="ignore")
        r = await detect_ai(text, use_llm_judge=False)
        label = "human" if "human" in f.stem.lower() else "ai"
        s = r.signals
        pred = "ai" if r.ai_probability >= 50 else "human"
        ok = pred == label
        correct += ok
        total += 1
        print(
            f"{f.name[:34]:34} {label:6} {s.get('binoculars', float('nan')):7.3f} {s.get('log_perplexity', float('nan')):7.3f} "
            f"{s.get('burstiness', float('nan')):6.3f} {s.get('stylometry_p', 0):6.2f} {r.ai_probability:6.1f}  {r.verdict} {'' if ok else '  <-- WRONG'}"
        )
    print(f"\naccuracy on bundled samples: {correct}/{total}")


if __name__ == "__main__":
    asyncio.run(main())
