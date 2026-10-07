"""Shared test helpers: fixture loading and the size knob for randomized sweeps.

``CD_TEST_SCALE`` (default 1) multiplies the number of random cases in the randomized
sweeps; e.g. ``CD_TEST_SCALE=20 python3 -m pytest -q tests/test_engine.py``.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(TESTS_DIR, "fixtures")
REPO_ROOT = os.path.dirname(TESTS_DIR)


def load_fixture(*parts: str) -> Dict[str, Any]:
    """Load a JSON file under ``tests/fixtures``."""
    with open(os.path.join(FIXTURES, *parts), "r", encoding="utf-8") as f:
        return json.load(f)


def paper_cases(name: str) -> List[Dict[str, Any]]:
    """Cases of ``tests/fixtures/paper/<name>.json``."""
    return load_fixture("paper", name + ".json")["cases"]


def sweep_scale() -> int:
    """Multiplier for randomized sweeps (env ``CD_TEST_SCALE``, default 1)."""
    try:
        return max(1, int(os.environ.get("CD_TEST_SCALE", "1")))
    except ValueError:
        return 1


@pytest.fixture(scope="session")
def scale() -> int:
    return sweep_scale()
