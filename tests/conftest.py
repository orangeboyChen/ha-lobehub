"""Pytest configuration for local imports."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STUBS = ROOT / "tests" / "stubs"

for path in (ROOT, STUBS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
