import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))

from synthetic.util import load_config  # noqa: E402


@pytest.fixture
def tmp_cfg(tmp_path):
    """Small config (2 series x 2 meetings) whose cache and outputs live in tmp_path."""
    cfg_path = tmp_path / "cfg.yaml"
    root_cfg = Path(__file__).resolve().parents[1] / "config" / "generation.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "extends": str(root_cfg),
        "generation": {"series_per_feature": {"F1": 1, "F2": 1, "F3": 0}, "meetings_per_series": 2,
                       "target_segments": [12, 16]},
        "paths": {k: str(tmp_path / k) for k in ("cache_dir", "raw_dir", "reviewed_dir", "splits_dir", "review_dir")},
    }), encoding="utf-8")
    return load_config(cfg_path)
