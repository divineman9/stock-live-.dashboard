"""Shared pytest configuration for the Dashboard Pro Pack test suite.

Ensures the project root (which contains the ``app`` package and
``streamlit_app.py``) is importable when tests are collected from any
working directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
