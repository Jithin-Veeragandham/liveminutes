"""`build` must be reproducible: zero API calls and byte-identical output on every run."""
import filecmp
from pathlib import Path

import pytest

from fake_llm import FakeClient
from synthetic import pipeline
from synthetic.pipeline import CacheMiss, run
from synthetic.util import ROOT, load_config, read_json


def _tree(d: Path) -> dict:
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


def _no_api(*a, **k):
    raise AssertionError("build must not create an LLM client")


def test_build_twice_is_byte_identical(tmp_cfg, tmp_path, monkeypatch):
    fake = FakeClient(tmp_cfg["model"], fail_first={"SY0101b"})
    monkeypatch.setattr(pipeline, "make_client", lambda cfg: fake)
    res = run(tmp_cfg, allow_api=True)
    assert res["meetings"] == 4 and not res["failed"]
    assert fake.calls == 5  # one retry for SY0101b

    monkeypatch.setattr(pipeline, "make_client", _no_api)
    run(tmp_cfg, allow_api=False, out_dir=tmp_path / "b1")
    run(tmp_cfg, allow_api=False, out_dir=tmp_path / "b2")
    t1, t2 = _tree(tmp_path / "b1"), _tree(tmp_path / "b2")
    assert t1 and t1.keys() == t2.keys()
    for k in t1:
        assert t1[k] == t2[k], f"{k} differs between builds"
    # generate's output and build's output are the same dataset too
    assert _tree(Path(tmp_cfg["paths"]["raw_dir"])) == t1

    report = read_json(tmp_path / "b1" / "validation_report.json")
    assert report["summary"]["retried"] == ["SY0101b"]
    assert any(not a["valid"] for a in report["attempts"])
    manifest = read_json(tmp_path / "b1" / "manifest.json")
    assert set(manifest["files"]) == set(t1) - {"manifest.json"}


def test_generate_reuses_cache(tmp_cfg, monkeypatch):
    fake = FakeClient(tmp_cfg["model"])
    monkeypatch.setattr(pipeline, "make_client", lambda cfg: fake)
    run(tmp_cfg, allow_api=True)
    n = fake.calls
    run(tmp_cfg, allow_api=True)
    assert fake.calls == n, "second generate must be all cache hits"


def test_build_on_empty_cache_fails_loudly(tmp_cfg, monkeypatch):
    monkeypatch.setattr(pipeline, "make_client", _no_api)
    with pytest.raises(CacheMiss):
        run(tmp_cfg, allow_api=False)


def test_failed_meeting_is_reported_not_dropped(tmp_cfg, monkeypatch):
    class AlwaysBad(FakeClient):
        def _draft(self, mid, user):
            d = super()._draft(mid, user)
            if mid == "SY0201a":
                d["edge_cases"] = []  # planned edge cases never tagged -> invalid on every attempt
            return d
    monkeypatch.setattr(pipeline, "make_client", lambda cfg: AlwaysBad(tmp_cfg["model"]))
    res = run(tmp_cfg, allow_api=True)
    assert [f["meeting_id"] for f in res["failed"]] == ["SY0201a"]
    assert res["blocked"] == ["SY0201b"]
    man = read_json(Path(tmp_cfg["paths"]["raw_dir"]) / "manifest.json")
    assert man["failed_meetings"] == ["SY0201a", "SY0201b"]


SAMPLES = ROOT / "samples" / "cache"


@pytest.mark.skipif(not SAMPLES.exists(), reason="no committed dry run yet")
def test_committed_dry_run_rebuilds_identically(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "make_client", _no_api)
    cfg = load_config("config/dryrun.yaml")
    run(cfg, allow_api=False, out_dir=tmp_path / "r")
    committed = ROOT / "samples" / "raw"
    cmp = filecmp.dircmp(committed, tmp_path / "r")
    assert _tree(committed) == _tree(tmp_path / "r"), cmp.report()
