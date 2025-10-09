"""Pytest configuration for the repository.

Ensures that the project root is importable when tests are executed from the
``tests`` directory by prepending the repository root to ``sys.path``.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
