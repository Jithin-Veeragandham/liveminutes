"""AMI data settings: where the data lives and which meetings make up the smoke test. Used by every AMI script."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))                       # 5_data/ami/
REPO = os.path.dirname(os.path.dirname(HERE))
RAW_DIR = os.path.join(REPO, "5_data", "raw", "ami")                    # downloaded data, gitignored
AMI_DIR = os.path.join(RAW_DIR, "ami_manual_1.6.2")                     # extracted AMI annotations
MANIFEST = os.path.join(HERE, "MANIFEST.json")                          # source, date, hashes (committed)
SPLIT_FILE = os.path.join(HERE, "ami_scenario_split.json")              # committed split

# Smoke-test subset: 3 development meetings, one per AMI site (ES = Edinburgh, IS = IDIAP, TS = TNO).
# Development meetings, never evaluation meetings, so a smoke run cannot leak test results.
SMOKE_MEETINGS = ["ES2003a", "IS1008a", "TS3004a"]
