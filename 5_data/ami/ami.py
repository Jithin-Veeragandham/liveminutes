"""Read one AMI meeting: its transcript lines (input) and its human-written summary (the reference).

Transcript = AMI's manual words, grouped into dialogue acts (one "line" each), sorted by time.
Reference  = the "abstract" section of AMI's human summary (abstractive/<meeting>.abssumm.xml).
"""
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import ami_settings as config

NITE = "{http://nite.sourceforge.net/}"
ROLE_NAMES = {"PM": "Project Manager", "ME": "Marketing Expert",
              "ID": "Industrial Designer", "UI": "User Interface Designer"}


@dataclass
class Line:
    speaker: str    # scenario role: PM, ME, ID or UI
    start: float    # seconds from the start of the meeting
    text: str


@dataclass
class Meeting:
    id: str
    lines: list       # transcript lines, sorted by start time
    reference: str    # human-written abstract (empty if the meeting has none)


# Map each speaker letter (A-D) of a meeting to its scenario role (PM, ME, ID, UI).
def speaker_roles(meeting):
    root = ET.parse(os.path.join(config.AMI_DIR, "corpusResources", "meetings.xml")).getroot()
    for m in root.iter("meeting"):
        if m.get("observation") == meeting:
            return {s.get("nxt_agent"): s.get("role") for s in m.iter("speaker")}
    raise KeyError(meeting)


# Read one speaker's words: {word id: (start time or None, text, is punctuation)}, in file order.
def read_words(meeting, agent):
    path = os.path.join(config.AMI_DIR, "words", f"{meeting}.{agent}.words.xml")
    words = {}
    for w in ET.parse(path).getroot():
        if w.tag == "w":   # skip vocal sounds, gaps and disfluency markers
            start = w.get("starttime")
            words[w.get(NITE + "id")] = (float(start) if start else None, w.text or "", w.get("punc") == "true")
    return words


# Join word tokens into text, attaching punctuation to the word before it ("okay ." -> "okay.").
def join_words(tokens):
    text = ""
    for word, is_punc in tokens:
        text += word if (is_punc or not text) else " " + word
    return text.strip()


# Build the transcript lines of one speaker from their dialogue acts.
def read_lines(meeting, agent, role):
    da_path = os.path.join(config.AMI_DIR, "dialogueActs", f"{meeting}.{agent}.dialog-act.xml")
    if not os.path.exists(da_path):
        return []
    words = read_words(meeting, agent)
    ids = list(words)
    position = {w: i for i, w in enumerate(ids)}
    lines = []
    for dact in ET.parse(da_path).getroot().iter("dact"):
        child = dact.find(NITE + "child")
        if child is None:
            continue
        # href looks like "ES2004a.A.words.xml#id(ES2004a.A.words0)..id(ES2004a.A.words5)"
        span = [part.split(")")[0] for part in child.get("href").split("id(")[1:]]
        if span[0] not in position or span[-1] not in position:
            continue
        span_ids = ids[position[span[0]]: position[span[-1]] + 1]
        times = [words[w][0] for w in span_ids if words[w][0] is not None]
        text = join_words([(words[w][1], words[w][2]) for w in span_ids])
        if text and times:
            lines.append(Line(role, min(times), text))
    return lines


# Read the human-written abstract of a meeting as one string.
def read_reference(meeting):
    path = os.path.join(config.AMI_DIR, "abstractive", f"{meeting}.abssumm.xml")
    if not os.path.exists(path):
        return ""
    root = ET.parse(path).getroot()
    return " ".join(s.text.strip() for a in root.iter("abstract") for s in a.iter("sentence") if s.text)


# Load one meeting: all speakers' lines merged in time order, plus the reference summary.
def load_meeting(meeting):
    lines = []
    for agent, role in speaker_roles(meeting).items():
        lines += read_lines(meeting, agent, role)
    return Meeting(meeting, sorted(lines, key=lambda l: (l.start, l.speaker)), read_reference(meeting))
