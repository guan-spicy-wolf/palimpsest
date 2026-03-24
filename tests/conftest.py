"""Shared fixtures for palimpsest tests."""

import sys
from pathlib import Path

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CONTRACTS_SRC = PROJECT_ROOT.parent / "yoitsu-contracts" / "src"
if str(CONTRACTS_SRC) not in sys.path:
    sys.path.insert(0, str(CONTRACTS_SRC))
