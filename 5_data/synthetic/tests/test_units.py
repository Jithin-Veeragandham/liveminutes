import copy
from pathlib import Path

import pytest

from fake_llm import FakeClient
from synthetic import pipeline, review
from synthetic.ami import convert_all
from synthetic.plan import build_plan
from synthetic.splits import assign
from synthetic.util import ROOT, load_config, load_scenarios, read_json, write_json
from synthetic.validate import validate_meeting

AMI = ROOT.parent / "ami_annotations" / "ami_public_manual_1.6.2"


def test_plan_is_deterministic_and_stable_when_counts_change():
    cfg = load_config("config/generation.yaml")
    _, sc = load_scenarios(cfg)
    p1, p2 = build_plan(cfg, sc), build_plan(cfg, sc)
    assert p1 == p2
    bigger = copy.deepcopy(cfg)
    bigger["generation"]["series_per_feature"]["F1"] += 2
    p3 = build_plan(bigger, sc)
    by_id = {s["series_id"]: s for s in p3}
    for s in p1:
        assert by_id[s["series_id"]] == s, "adding series must not change existing ones"


def test_every_series_covers_all_its_scenarios():
    cfg = load_config("config/generation.yaml")
    _, sc = load_scenarios(cfg)
    for s in build_plan(cfg, sc):
        want = {x["id"] for x in sc if x["feature"] == s["feature"]}
        got = {i for m in s["meetings"] for i in m["scenario_ids"]}
        assert got == want


def test_absent_scenarios_get_an_absent_participant():
    cfg = load_config("config/generation.yaml")
    _, sc = load_scenarios(cfg)
    needs = {x["id"] for x in sc if x["needs_absent"]}
    for s in build_plan(cfg, sc):
        for m in s["meetings"]:
            n_abs = sum(p["status"] == "absent" for p in m["participants"])
            assert n_abs == (1 if needs & set(m["scenario_ids"]) else 0)


def test_splits_whole_series_and_all_nonempty():
    index = [{"series_id": f"SY0{f}0{i}", "feature": f"F{f}", "meeting_id": f"SY0{f}0{i}{l}"}
             for f in (1, 2, 3) for i in (1, 2, 3) for l in "abcd"]
    a = assign(index, 6170, {"train": 0.6, "dev": 0.2, "test": 0.2})
    assert a == assign(index, 6170, {"train": 0.6, "dev": 0.2, "test": 0.2})
    assert not a["empty_splits"]
    for f in ("F1", "F2", "F3"):
        assert {v["split"] for v in a["series"].values() if v["feature"] == f} == {"train", "dev", "test"}
    seen = {}
    for split, ms in a["splits"].items():
        for m in ms:
            assert seen.setdefault(m[:-1], split) == split


def test_validator_catches_planted_errors(tmp_cfg, monkeypatch):
    monkeypatch.setattr(pipeline, "make_client", lambda cfg: FakeClient(tmp_cfg["model"]))
    pipeline.run(tmp_cfg, allow_api=True)
    m = read_json(Path(tmp_cfg["paths"]["raw_dir"]) / "meetings" / "SY0201a.json")
    assert validate_meeting(m) == []
    bad = copy.deepcopy(m)
    bad["segments"][3]["start"] = 0.0
    bad["action_items"][1]["owner"] = "E"
    bad["participants"][0]["status"] = "absent"
    bad["segments"][0]["speaker"] = bad["participants"][0]["label"]
    errs = " | ".join(validate_meeting(bad))
    assert "not monotonic" in errs and "not a listed participant" in errs and "absent participant" in errs


def test_review_roundtrip(tmp_cfg, monkeypatch):
    monkeypatch.setattr(pipeline, "make_client", lambda cfg: FakeClient(tmp_cfg["model"]))
    pipeline.run(tmp_cfg, allow_api=True)
    raw, rd = Path(tmp_cfg["paths"]["raw_dir"]), Path(tmp_cfg["paths"]["review_dir"])
    raw_before = {p: p.read_bytes() for p in raw.rglob("*") if p.is_file()}
    review.export(raw, rd)
    forms = sorted((rd / "forms").glob("*.json"))
    assert len(forms) == 4 and (rd / "export" / "SY0101a.md").exists()
    for i, fp in enumerate(forms):
        f = read_json(fp)
        for it in f["items"]:
            it["reviewer_1"]["decision"] = it["reviewer_2"]["decision"] = "accept"
        if i == 0:
            it = next(x for x in f["items"] if x["item_id"] == "summary.abstract")
            it["reviewer_1"]["decision"] = "edit"
            it["reviewer_1"]["edit"] = ["Edited abstract."]
            it["resolution"].update(decision="edit", edit=["Edited abstract."], name="third")
        if i == 1:
            f["items"][0]["reviewer_1"]["decision"] = f["items"][0]["reviewer_2"]["decision"] = "reject"
        write_json(fp, f)
    ag = review.agreement(rd)
    assert ag["items_pending"] == 0 and len(ag["disagreements"]) == 1
    man = review.apply(raw, rd, Path(tmp_cfg["paths"]["reviewed_dir"]), "v1")
    assert len(man["kept"]) == 3 and len(man["excluded"]) == 1
    edited = read_json(Path(tmp_cfg["paths"]["reviewed_dir"]) / "v1" / "meetings" / f"{forms[0].name.split('.')[0]}.json")
    assert edited["summary"]["abstract"] == ["Edited abstract."]
    assert {p: p.read_bytes() for p in raw.rglob("*") if p.is_file()} == raw_before
    with pytest.raises(review.ReviewError):
        review.apply(raw, rd, Path(tmp_cfg["paths"]["reviewed_dir"]), "v1")


@pytest.mark.skipif(not AMI.exists(), reason="AMI manual annotations not available")
def test_ami_converter_es2002a():
    (m,) = list(convert_all(AMI, ["ES2002a"]))
    assert validate_meeting(m) == []
    assert {p["label"]: p["role"] for p in m["participants"]} == {"A": "ID", "B": "PM", "C": "UI", "D": "ME"}
    assert m["segments"][2]["text"] == "Um well this is the kick-off meeting for our our project."
    assert m["summary"]["decisions"][0] == "The remote will sell for 25 Euro."
    assert [a["owner"] for a in m["action_items"]] == ["A", "C", "D"]
