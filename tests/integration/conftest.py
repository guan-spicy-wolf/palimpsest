"""Shared fixtures for integration tests."""

import sys
from pathlib import Path

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Add palimpsest src
PALIMPSEST_SRC = PROJECT_ROOT / "palimpsest"
if str(PALIMPSEST_SRC) not in sys.path:
    sys.path.insert(0, str(PALIMPSEST_SRC))

# Add trenni src
TRENNI_SRC = PROJECT_ROOT / "trenni"
if str(TRENNI_SRC) not in sys.path:
    sys.path.insert(0, str(TRENNI_SRC))

# Add yoitsu-contracts src
CONTRACTS_SRC = PROJECT_ROOT / "yoitsu-contracts" / "src"
if str(CONTRACTS_SRC) not in sys.path:
    sys.path.insert(0, str(CONTRACTS_SRC))