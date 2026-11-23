"""Deterministic meeting plan: which series exist, and per meeting the roles,
absences, product and edge-case scenarios. Depends only on config + scenarios + seed.

Each series gets its own RNG seeded from (seed, series_id), so adding a series
never changes the plan, and therefore the cache keys, of the existing ones.
"""
from __future__ import annotations

import hashlib
import math
import random

FEATURES = ["F1", "F2", "F3"]
LETTERS = "abcd"
LABELS = ["A", "B", "C", "D"]
ROLES = ["PM", "ME", "UI", "ID"]
ROLE_NAMES = {"PM": "Project Manager", "ME": "Marketing Expert",
              "UI": "User Interface designer", "ID": "Industrial Designer"}
STAGES = {"a": "kickoff", "b": "functional design", "c": "conceptual design", "d": "detailed design"}


def series_rng(seed: int, series_id: str, purpose: str) -> random.Random:
    h = hashlib.sha256(f"{seed}:{series_id}:{purpose}".encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def series_id_for(feature: str, index: int) -> str:
    """F1 -> SY01xx, F2 -> SY02xx, F3 -> SY03xx (index is 1-based)."""
    return f"SY{FEATURES.index(feature) + 1:02d}{index:02d}"


def build_plan(cfg: dict, scenarios: list[dict]) -> list[dict]:
    gen = cfg["generation"]
    seed = gen["seed"]
    m = gen["meetings_per_series"]
    if not 1 <= m <= 4:
        raise ValueError("meetings_per_series must be 1..4 (AMI letters a-d)")
    by_feature = {f: [s for s in scenarios if s["feature"] == f] for f in FEATURES}
    plan = []
    for feature in FEATURES:
        n = gen["series_per_feature"].get(feature, 0)
        cap = gen.get("max_series_per_feature", {}).get(feature, 99)
        if n > cap:
            raise ValueError(f"series_per_feature.{feature}={n} exceeds max {cap}")
        feats = by_feature[feature]
        if n and not feats:
            raise ValueError(f"no scenarios for {feature}")
        for i in range(1, n + 1):
            sid = series_id_for(feature, i)
            rng = series_rng(seed, sid, "plan")
            roles = ROLES[:]
            rng.shuffle(roles)
            role_of = dict(zip(LABELS, roles))
            product = rng.choice(gen["products"])
            order = [s["id"] for s in feats]
            rng.shuffle(order)
            k = min(gen["scenarios_per_meeting"], len(order))
            slots = m * k
            # Cycle through the shuffled scenarios: every scenario appears before
            # any repeats, and k consecutive cyclic items are always distinct.
            seq = (order * math.ceil(slots / len(order)))[:slots]
            meetings = []
            for j, letter in enumerate(LETTERS[:m]):
                ids = seq[j * k:(j + 1) * k]
                needs_absent = any(s["needs_absent"] for s in feats if s["id"] in ids)
                absent = None
                if needs_absent:
                    absent = rng.choice([lab for lab in LABELS if role_of[lab] != "PM"])
                meetings.append({
                    "meeting_id": f"{sid}{letter}",
                    "series_id": sid,
                    "meeting_letter": letter,
                    "stage": STAGES[letter],
                    "feature": feature,
                    "product": product,
                    "participants": [
                        {"label": lab, "role": role_of[lab],
                         "status": "absent" if lab == absent else "present"}
                        for lab in LABELS
                    ],
                    "scenario_ids": ids,
                    "target_segments": list(gen["target_segments"]),
                })
            plan.append({"series_id": sid, "feature": feature, "product": product, "meetings": meetings})
    return plan


def coverage(plan: list[dict]) -> dict:
    """Counts used by the README / explainer: scenario occurrences and pairings."""
    occ, pairs = {}, {}
    for s in plan:
        for mt in s["meetings"]:
            for sc in mt["scenario_ids"]:
                occ[sc] = occ.get(sc, 0) + 1
            key = " + ".join(sorted(mt["scenario_ids"]))
            pairs[key] = pairs.get(key, 0) + 1
    return {"occurrences": dict(sorted(occ.items())), "combinations": dict(sorted(pairs.items()))}
