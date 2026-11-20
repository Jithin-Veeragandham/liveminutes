"""Breakdown groups for the action-item scores. FIXED BEFORE ANY RESULT WAS LOOKED AT (see the git history of this
file); don't add or change groups after seeing numbers.

AMI (one label per meeting; a team's 4 meetings share the speaker groups, since the same 4 people recorded them):
  speakers   all native English / mixed / all non-native
  gender     all male / mixed / all female
  type       a (kickoff) / b+c (middle) / d (final)       -- the series letter
  Sex and native language come from the AMI participant id: <sex M/F><site E/I/T><language E = native English,
  anything else = not>, e.g. MEO015 = male, Edinburgh, non-native. participants.xml has no entry for the TNO (TS)
  speakers (ids like MTD013PM: D = Dutch), so the id is the one source that covers everyone; where
  participants.xml has an entry, it agrees (tests check this).
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


def ami_groups(meeting, participants):
    ids = [pid for _, _, pid in participants[meeting]]
    native = sum(1 for p in ids if p[2:3] == "E")
    female = sum(1 for p in ids if p[0] == "F")
    letter = meeting[-1]
    return {
        "speakers": "all native" if native == len(ids) else "all non-native" if native == 0 else "mixed",
        "gender": "all male" if female == 0 else "all female" if female == len(ids) else "mixed",
        "type": "a (kickoff)" if letter == "a" else "d (final)" if letter == "d" else "b+c (middle)",
    }


def synthetic_groups(d, series_feature):
    return {"feature": series_feature}
