"""pytest setup for 1_ingestion's tests: import the bridge's modules (bridge, ui, ...) from 1_ingestion/."""

import os
import sys
from pathlib import Path

# Tests never call a real LLM, whatever the repo's .env says: with this provider the live notes are off
# (notes.LiveNotes reason "unknown LLM provider"), so pages show the sample data. Tests that need the notes pass
# their own fake provider (tests/test_notes.py, test_llm_ui.py).
os.environ["LIVEMINUTES_LLM_PROVIDER"] = "none"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
