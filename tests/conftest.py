"""Test environment: isolated data directory, no engine token, no model downloads, no real keys.
Set before any `app.*` import so `app.config` picks these up instead of the developer's .env."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["BURSTAI_DATA_DIR"] = tempfile.mkdtemp(prefix="burstai-test-")
os.environ["BURSTAI_ENGINE_TOKEN"] = ""
os.environ["BURSTAI_OFFLINE_LM"] = "1"
os.environ["BURSTAI_PRELOAD"] = "0"
os.environ["GEMINI_API_KEY"] = ""
os.environ["BURSTAI_PUBLIC_URL"] = ""
