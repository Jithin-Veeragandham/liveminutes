"""The one-command smoke test: both baselines on 2 AMI dev + 1 synthetic dev meeting, scored, in under 2 minutes.
Needs the AMI download, the synthetic-data commit and the two pinned models (downloaded on the first run of
3_action_items/run.py); skipped if a model isn't downloaded yet."""
import os
import subprocess
import sys
import time

import pytest

import settings

REPO = settings.REPO


def downloaded(repo_id, revision):
    from huggingface_hub import try_to_load_from_cache
    return isinstance(try_to_load_from_cache(repo_id, "config.json", revision=revision), str)


@pytest.mark.skipif(not (downloaded(settings.ZEROSHOT["model"], settings.ZEROSHOT["revision"])
                         and downloaded(settings.MATCHING["embedder"], settings.MATCHING["revision"])),
                    reason="pinned models not downloaded yet (run 3_action_items/run.py --smoke once)")
def test_smoke_runs_in_under_two_minutes(tmp_path):
    env = {**os.environ, "HF_HUB_OFFLINE": "1"}
    start = time.perf_counter()
    done = subprocess.run([sys.executable, os.path.join(REPO, "3_action_items", "run.py"), "--smoke", "--no-log"],
                          cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
    seconds = time.perf_counter() - start
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    out = done.stdout
    for name, source in (("rules", "ami"), ("zeroshot", "ami"), ("rules", "synthetic"), ("zeroshot", "synthetic")):
        assert f"{name} on {source} smoke" in out
    assert out.count("precision ") == 4 and "not logged (--no-log)" in out
    assert seconds < 120, f"smoke test took {seconds:.0f} s"
