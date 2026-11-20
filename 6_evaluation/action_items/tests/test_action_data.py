"""The datasets as the baselines see them. Needs the AMI download (5_data/raw/ami/) and the synthetic-data commit
(git fetch origin synthetic-data); skipped if either is missing."""
import os
import subprocess
import xml.etree.ElementTree as ET

import pytest

import ami_settings
import data

needs_ami = pytest.mark.skipif(not os.path.isdir(ami_settings.AMI_DIR), reason="AMI not downloaded")
needs_synthetic = pytest.mark.skipif(
    subprocess.run(["git", "cat-file", "-e", data.SYNTHETIC_COMMIT], cwd=data.REPO, capture_output=True).returncode != 0,
    reason="synthetic-data commit not fetched")


@needs_ami
def test_ami_lines_are_the_same_as_ami_py():
    import ami
    for mid in data.SMOKE["ami"]:
        ours = [(l[0], l[1], l[2]) for l in data.ami_lines(mid)]
        theirs = [(l.start, l.speaker, l.text) for l in ami.load_meeting(mid).lines]
        assert ours == theirs


@needs_ami
def test_ami_eval_and_dev_load_with_gold_and_groups():
    meetings, info = data.load("ami", "eval")
    assert len(meetings) == 20 and info["data_hash"].startswith("1ce0450de10c")
    assert sum(len(m.gold) for m in meetings) > 0
    assert all(set(m.groups) == {"speakers", "gender", "type"} for m in meetings)
    assert all(len({l["id"] for l in m.lines}) == len(m.lines) for m in meetings)
    for m in meetings:
        assert not any(g.text.strip("*").upper() == "NA" for g in m.gold)


@needs_ami
def test_participant_id_letters_and_participants_xml():
    """Sex: the id letter always agrees. Native English: agrees except MEO022 (a training meeting), which is why
    participants.xml is used first."""
    root = ET.parse(os.path.join(ami_settings.AMI_DIR, "corpusResources", "participants.xml")).getroot()
    checked, disagree = 0, []
    for p in root.iter("participant"):
        pid = p.get("{http://nite.sourceforge.net/}id")
        assert (pid[0] == "F") == (p.get("sex") == "F"), pid
        if (pid[2] == "E") != (p.get("native_language") == "English"):
            disagree.append(pid)
        checked += 1
    assert checked == 189 and disagree == ["MEO022"]
    import split
    s = split.load_split()
    used = {pid for m in s["dev"] + s["eval"] for _, _, pid in data.ami_participants()[m]}
    assert "MEO022" not in used


@needs_synthetic
def test_synthetic_test_split_and_gold():
    meetings, info = data.load("synthetic", "test")
    assert len(meetings) == 20 and info["commit"] == data.SYNTHETIC_COMMIT
    assert sum(len(m.gold) for m in meetings) == 112     # active + reassigned; 4 dropped tasks left out
    assert all(m.edge_cases for m in meetings)
    assert {m.groups["feature"] for m in meetings} == {"F1", "F2", "F3"}


@needs_synthetic
def test_synthetic_splits_do_not_overlap():
    splits, _ = data.synthetic_split()
    s = {k: set(v) for k, v in splits["splits"].items()}
    assert not (s["dev"] & s["test"]) and not (s["train"] & s["test"]) and not (s["train"] & s["dev"])
    assert set(data.SMOKE["synthetic"]) <= s["dev"]
