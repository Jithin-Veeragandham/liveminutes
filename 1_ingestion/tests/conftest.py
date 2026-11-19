"""pytest setup for 1_ingestion's tests: import the bridge's modules (bridge, ui, ...) from 1_ingestion/."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
