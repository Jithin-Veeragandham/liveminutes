"""Breakdown groups for the action-item scores. FIXED BEFORE ANY RESULT WAS LOOKED AT (see the git history of this
file); don't add or change groups after seeing numbers.

AMI (one label per meeting; a team's 4 meetings share the speaker groups, since the same 4 people recorded them):
  speakers   all native English / mixed / all non-native
  gender     all male / mixed / all female
  type       a (kickoff) / b+c (middle) / d (final)       -- the series letter
  Native language: participants.xml when it has an entry for the person; else the AMI participant id
  <sex M/F><site E/I/T><language E = native English, anything else = not>. participants.xml has no entry for any
  TNO (TS) speaker (ids like MTD013PM: D = Dutch). Where both exist they agree for 188 of 189 people; the one
  exception (MEO022, "English" in participants.xml) is in a training meeting. Sex: the id's first letter (agrees
  with participants.xml for everyone; tests check both).
  No earlier (Week 9) grouping exists in the repo, so these are new.

Synthetic:
  edge case  each tagged edge case (13 scenario types in 5_data/synthetic/scenarios/scenarios.yaml), scored on the
             items in its tagged lines: gold items whose evidence is in those lines, predictions whose source
             lines are (see metrics.region_counts). n = tagged instances.
  feature    the series' target feature (F1 summary, F2 action items, F3 Q&A): every series has action items, but
             only F2 series were written to stress them.

Every group reports its n. Groups with n <= SMALL_N meetings (or instances) are flagged "too small to read".
"""

SMALL_N = 4
AMI_GROUPS = ["speakers", "gender", "type"]
SYNTHETIC_GROUPS = ["feature"]


def ami_groups(meeting, participants, native_language=None):
    """participants: data.ami_participants(); native_language: {participant id: language} from participants.xml."""
    ids = [pid for _, _, pid in participants[meeting]]
    known = native_language or {}
    native = sum(1 for p in ids if (known[p] == "English" if p in known else p[2:3] == "E"))
    female = sum(1 for p in ids if p[0] == "F")
    letter = meeting[-1]
    return {
        "speakers": "all native" if native == len(ids) else "all non-native" if native == 0 else "mixed",
        "gender": "all male" if female == 0 else "all female" if female == len(ids) else "mixed",
        "type": "a (kickoff)" if letter == "a" else "d (final)" if letter == "d" else "b+c (middle)",
    }


def synthetic_groups(d, series_feature):
    return {"feature": series_feature}
