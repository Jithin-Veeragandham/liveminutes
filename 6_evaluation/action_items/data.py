"""The meetings the action-item baselines are run and scored on: AMI and the synthetic meetings, in one shape.

    meetings, info = load("ami", "dev")          # or ("synthetic", "test"); info = hashes for the results log

Each Meeting has
  lines   the transcript in the UI contract's line format (docs/UI_CONTRACT.md):
          {"id": "t<start ms>", "speaker": display name, "start": s, "end": s, "text": ...}
  people  who was in the meeting: a key (what gold owners use), a display name and the aliases people use
  gold    the human (AMI) or generated (synthetic) action items: text, owners, due, evidence lines
  groups  the breakdown labels (breakdowns.py)

AMI (5_data/ami, read only):
  transcript  the same lines as 5_data/ami/ami.py builds (one per dialogue act), plus each line's dialogue act id
  gold        the "actions" section of the human summary (abstractive/<meeting>.abssumm.xml). "*NA*" = none.
  owners      AMI actions name roles ("The Industrial Designer will ..."). The roles named before the verb become the
              owners (ami_owners below). No role, or "the team", "all participants": unassigned.
  evidence    the transcript lines each action sentence was written from (extractive/<meeting>.summlink.xml, read
              from the downloaded zip; fetch_data.py doesn't extract that folder). Used to set the match threshold.
  speakers    shown by role (speaker "PM" -> "Project Manager"), as ami.py labels them.

Synthetic (5_data/synthetic on the synthetic-data branch, read only, from git at SYNTHETIC_COMMIT):
  transcript  the segments; gold = action_items with status active or reassigned (a dropped task is not an action
              item at the end of the meeting); owner = the final owner's label, null = unassigned.
  evidence    each item's evidence_segments and history segments.
  split       its own data/splits/synthetic_splits.json (train / dev / test).
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "5_data", "ami"))
sys.path.insert(0, os.path.join(REPO, "3_action_items"))
import ami  # noqa: E402
import ami_settings  # noqa: E402
from due import due_key  # noqa: E402

# The synthetic data lives on the synthetic-data branch (not on main yet). Read it from this exact commit.
SYNTHETIC_COMMIT = "d0cbb4b3670d12611553648b2b571cc0f4efc914"
SYNTHETIC_DIR = "5_data/synthetic/data"
SMOKE = {"ami": ["ES2003a", "IS1008a"], "synthetic": ["SY0201a"]}   # dev meetings only


@dataclass
class Person:
    key: str              # what gold owners refer to: AMI role code ("PM"), synthetic label ("A")
    name: str             # display name in the transcript ("Project Manager", "Helen")
    aliases: tuple        # lowercase strings an owner field may use for them ("helen", "b", "pm", "project manager")
    mentions: tuple = ()  # words that name them in speech ("helen", "marketing expert"); never "a" or "me"
    present: bool = True


@dataclass
class Gold:
    id: str
    text: str
    owners: frozenset     # person keys; empty = unassigned (the team, nobody)
    due: str              # due_key() of the gold text ("" = none said)
    evidence: frozenset   # line ids


@dataclass
class Meeting:
    id: str
    source: str
    lines: list
    people: list
    gold: list
    groups: dict = field(default_factory=dict)
    edge_cases: list = field(default_factory=list)   # synthetic: [(scenario id, frozenset of line ids)]

    def names(self):
        return [p.name for p in self.people]


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def combined_hash(files):
    """sha256 over sorted '<name>  <sha256>' lines, the same method as fetch_data.data_hash."""
    text = "".join(f"{name}  {digest}\n" for name, digest in sorted(files.items()))
    return hashlib.sha256(text.encode()).hexdigest()


def line_id(start_s, used):
    """'t' + start in ms (the UI contract's line id); +1 ms until it is unique in the meeting."""
    ms = int(round(start_s * 1000))
    while f"t{ms}" in used:
        ms += 1
    used.add(f"t{ms}")
    return f"t{ms}"


# ================= AMI =================

ROLE_ALIASES = {
    "PM": ("project manager", "the project manager", "pm", "manager", "the manager"),
    "ME": ("marketing expert", "the marketing expert", "marketing", "me", "the marketing person"),
    "ID": ("industrial designer", "the industrial designer", "id", "industrial design"),
    "UI": ("user interface designer", "the user interface designer", "user interface", "ui designer",
           "interface designer", "ui", "the interface designer"),
}

# How people name a role when they speak ("the marketing expert will ..."). Only person nouns: "user interface"
# alone is the thing being designed, and "me" is a pronoun.
ROLE_MENTIONS = {
    "PM": ("project manager",),
    "ME": ("marketing expert", "marketing person"),
    "ID": ("industrial designer",),
    "UI": ("user interface designer", "interface designer", "ui designer"),
}

# Role words in an AMI action sentence, for its owner. "designers" (plural) = both designers.
ROLE_PATTERNS = [
    ("PM", r"project manager|\bpm\b|\bmanager\b"),
    ("ME", r"marketing"),
    ("ID", r"industrial design"),
    ("UI", r"user interface|interface designer|\bui\b"),
]


MODAL = r"(?:will|would|shall|should|must|is to|are to|has to|have to|needs? to|is going to|are going to)"


def roles_in(text):
    owners = {role for role, pattern in ROLE_PATTERNS if re.search(pattern, text)}
    if re.search(r"\bdesigners\b", text):   # "the designers" = both designers
        owners |= {"ID", "UI"}
    return owners


def ami_owners(sentence, previous=frozenset()):
    """The roles an AMI action sentence gives the task to.
    - the subject, i.e. the words before the first verb: 'The Industrial Designer and User Interface Designer will
      work on a prototype' -> {ID, UI}. 'The team will decide how the user interface will work' -> {} (unassigned).
    - 'X instructed / asked / told Y to ...' -> Y.
    - a second task in the sentence: '... and the Industrial Designer will work on ...' adds ID.
    - 'He / She will also ...' -> the owners of the sentence before."""
    s = sentence.lower()
    told = re.search(r"\b(?:instructed|asked|told|assigned|requested|tasked|charged)\b(.*?)\bto\b", s)
    if told and roles_in(told.group(1)):
        return frozenset(roles_in(told.group(1)))
    verb = re.search(r"\b(will|would|shall|should|must|is|are|was|were|has|have|had|needs?|to|can|could|"
                     r"agreed|decided|plans?|wants?|got|received|gets?)\b", s)
    owners = roles_in(s[:verb.start()] if verb else s)
    for role, mentions in ROLE_MENTIONS.items():   # a person noun right before a modal: a second task
        if re.search(rf"\b(?:{'|'.join(mentions)})\s+{MODAL}\b", s):
            owners.add(role)
    if not owners and re.match(r"\s*(he|she)\b", s):
        owners = set(previous)
    return frozenset(owners)


def ami_lines(meeting):
    """The transcript lines exactly as ami.read_lines builds them, plus each line's dialogue act id."""
    rows = []
    for agent, role in ami.speaker_roles(meeting).items():
        path = os.path.join(ami_settings.AMI_DIR, "dialogueActs", f"{meeting}.{agent}.dialog-act.xml")
        if not os.path.exists(path):
            continue
        words = ami.read_words(meeting, agent)
        word_ids = list(words)
        position = {w: i for i, w in enumerate(word_ids)}
        for dact in ET.parse(path).getroot().iter("dact"):
            child = dact.find(ami.NITE + "child")
            if child is None:
                continue
            first, last = ami.word_range(child.get("href"))
            if first not in position or last not in position:
                continue
            span = word_ids[position[first]: position[last] + 1]
            text = ami.join_words([(words[w][1], words[w][2]) for w in span])
            times = [words[w][0] for w in span if words[w][0] is not None]
            if text and times:
                rows.append((min(times), role, text, dact.get(ami.NITE + "id")))
    return sorted(rows, key=lambda r: (r[0], r[1]))   # the same order as ami.load_meeting


def ami_gold_sentences(meeting):
    """[(sentence id, text)] of the summary's actions section, without the '*NA*' placeholder."""
    path = os.path.join(ami_settings.AMI_DIR, "abstractive", f"{meeting}.abssumm.xml")
    if not os.path.exists(path):
        return []
    root = ET.parse(path).getroot()
    out = []
    for section in root.iter("actions"):
        for s in section.iter("sentence"):
            text = (s.text or "").strip()
            if text and text.strip("*").upper() not in ("NA", "N/A", "NONE"):
                out.append((s.get(ami.NITE + "id"), text))
    return out


def ami_evidence(zip_file, meeting):
    """{abstractive sentence id: {dialogue act ids}} from extractive/<meeting>.summlink.xml in the zip."""
    name = f"extractive/{meeting}.summlink.xml"
    if name not in zip_file.namelist():
        return {}, None
    data = zip_file.read(name)
    links = {}
    for link in ET.fromstring(data).iter("summlink"):
        ptr = {p.get("role"): p.get("href").split("id(")[-1].rstrip(")") for p in link.iter(ami.NITE + "pointer")}
        if "abstractive" in ptr and "extractive" in ptr:
            links.setdefault(ptr["abstractive"], set()).add(ptr["extractive"])
    return links, sha256_bytes(data)


def ami_participants():
    """{meeting: [(agent letter, role, participant id)]} from meetings.xml."""
    root = ET.parse(os.path.join(ami_settings.AMI_DIR, "corpusResources", "meetings.xml")).getroot()
    return {m.get("observation"): [(s.get("nxt_agent"), s.get("role"), s.get("global_name")) for s in m.iter("speaker")]
            for m in root.iter("meeting")}


def load_ami_meeting(meeting, zip_file, participants):
    used, lines, by_dact = set(), [], {}
    for start, role, text, dact in ami_lines(meeting):
        lid = line_id(start, used)
        lines.append({"id": lid, "speaker": ami.ROLE_NAMES.get(role, role), "start": start, "end": start, "text": text})
        by_dact[dact] = lid
    people = [Person(role, ami.ROLE_NAMES.get(role, role), ROLE_ALIASES.get(role, (role.lower(),)),
                     ROLE_MENTIONS.get(role, ()))
              for _, role, _ in participants[meeting]]
    links, digest = ami_evidence(zip_file, meeting)
    gold, previous = [], frozenset()
    for sid, text in ami_gold_sentences(meeting):
        previous = ami_owners(text, previous)
        gold.append(Gold(sid, text, previous, due_key(text),
                         frozenset(by_dact[d] for d in links.get(sid, ()) if d in by_dact)))
    m = Meeting(meeting, "ami", lines, people, gold)
    return m, digest


def load_ami(ids):
    import fetch_data
    data_hash = fetch_data.verify() if os.path.isdir(ami_settings.AMI_DIR) else fetch_data.fetch()
    manifest = fetch_data.load_manifest()
    zip_path = os.path.join(ami_settings.RAW_DIR, manifest["file"])
    if fetch_data.sha256_file(zip_path) != manifest["sha256"]:
        sys.exit(f"{zip_path} does not match MANIFEST.json; delete 5_data/raw/ami/ and run again")
    participants = ami_participants()
    meetings, evidence = [], {}
    with zipfile.ZipFile(zip_path) as z:
        for mid in ids:
            m, digest = load_ami_meeting(mid, z, participants)
            meetings.append(m)
            if digest:
                evidence[f"extractive/{mid}.summlink.xml"] = digest
    from breakdowns import ami_groups
    for m in meetings:
        m.groups = ami_groups(m.id, participants)
    return meetings, {"data_hash": data_hash, "zip_sha256": manifest["sha256"],
                      "evidence_hash": combined_hash(evidence), "evidence_files": len(evidence)}


# ================= Synthetic =================

def git_files(paths, commit=SYNTHETIC_COMMIT):
    """{path: bytes} for files at a commit, read from git objects (no checkout, the branch is never changed)."""
    if subprocess.run(["git", "cat-file", "-e", commit], cwd=REPO, capture_output=True).returncode:
        sys.exit(f"synthetic data commit {commit[:12]} isn't in this clone. Run:  git fetch origin synthetic-data")
    proc = subprocess.run(["git", "cat-file", "--batch"], cwd=REPO, capture_output=True,
                          input="".join(f"{commit}:{p}\n" for p in paths).encode())
    out, data, i = {}, proc.stdout, 0
    for p in paths:
        header_end = data.index(b"\n", i)
        header = data[i:header_end].split()
        if header[-1] == b"missing":
            sys.exit(f"{p} is missing at {commit[:12]}")
        size = int(header[2])
        out[p] = data[header_end + 1: header_end + 1 + size]
        i = header_end + 1 + size + 1
    return out


def synthetic_split():
    raw = git_files([f"{SYNTHETIC_DIR}/splits/synthetic_splits.json"])
    path, data = next(iter(raw.items()))
    return json.loads(data), {path: sha256_bytes(data)}


def synthetic_people(d):
    out = []
    for p in d["participants"]:
        role = p.get("role") or ""
        name = p.get("name") or p["label"]
        aliases = {name.lower(), p["label"].lower()} | set(ROLE_ALIASES.get(role, ()))
        mentions = (name.lower(),) + ROLE_MENTIONS.get(role, ())
        out.append(Person(p["label"], name, tuple(sorted(aliases)), mentions, p.get("status") == "present"))
    return out


def load_synthetic_meeting(d, series_feature):
    people = synthetic_people(d)
    name = {p.key: p.name for p in people}
    used, lines, by_seg = set(), [], {}
    for seg in d["segments"]:
        lid = line_id(seg["start"], used)
        lines.append({"id": lid, "speaker": name.get(seg["speaker"], seg["speaker"]), "start": seg["start"],
                      "end": seg.get("end", seg["start"]), "text": seg["text"]})
        by_seg[seg["id"]] = lid
    gold = []
    for a in d["action_items"]:
        if a["status"] == "dropped":
            continue
        segs = set(a.get("evidence_segments") or []) | {h["segment"] for h in a.get("history") or []}
        owners = frozenset([a["owner"]]) if a.get("owner") and not a.get("owner_unclear") else frozenset()
        gold.append(Gold(a["id"], a["description"], owners, due_key(a["description"]),
                         frozenset(by_seg[s] for s in segs if s in by_seg)))
    edges = [(e["scenario_id"], frozenset(by_seg[s] for s in e["segments"] if s in by_seg)) for e in d["edge_cases"]]
    m = Meeting(d["meeting_id"], "synthetic", lines, people, gold, edge_cases=edges)
    from breakdowns import synthetic_groups
    m.groups = synthetic_groups(d, series_feature)
    return m


def load_synthetic(ids):
    splits, files = synthetic_split()
    feature = {mid: s["feature"] for s in splits["series"].values() for mid in s["meetings"]}
    raw = git_files([f"{SYNTHETIC_DIR}/raw/meetings/{mid}.json" for mid in ids])
    meetings = []
    for path, data in raw.items():
        files[path] = sha256_bytes(data)
        meetings.append(load_synthetic_meeting(json.loads(data), feature[path.rsplit("/", 1)[1][:-5]]))
    print(f"synthetic data OK  (commit {SYNTHETIC_COMMIT[:12]}, {len(raw)} meetings, data_hash {combined_hash(files)[:12]}...)")
    return meetings, {"data_hash": combined_hash(files), "commit": SYNTHETIC_COMMIT, "files": len(files)}


# ================= Splits =================

def split_ids(source, split):
    """Meeting ids of a split. AMI: dev / eval (5_data/ami split); synthetic: dev / test (its own split)."""
    if split == "smoke":
        return SMOKE[source]
    if source == "ami":
        import split as ami_split
        names = {"test": "eval"}
        return ami_split.load_split()[names.get(split, split)]
    splits, _ = synthetic_split()
    names = {"eval": "test"}
    return splits["splits"][names.get(split, split)]


def load(source, split, ids=None):
    """(meetings, info) for 'ami' or 'synthetic' and a split: smoke | dev | eval/test."""
    ids = ids or split_ids(source, split)
    meetings, info = load_ami(ids) if source == "ami" else load_synthetic(ids)
    return meetings, {**info, "source": source, "split": split, "meetings": [m.id for m in meetings]}
