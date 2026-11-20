"""Deterministic stand-in for the LLM client, used only by tests.

It reads the planned scenarios and the absent participant from the rendered prompt
and returns a small draft that satisfies those scenarios' checks. Its output is wrapped
the same way `claude -p --output-format json` wraps a response.
"""
import json
import re


class FakeClient:
    def __init__(self, model_cfg, fail_first=()):
        self.model = model_cfg["version"]
        self.fail_first = set(fail_first)  # meeting IDs whose attempt 0 returns junk
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        mid = re.search(r"Write meeting (\S+):", user).group(1)
        if mid in self.fail_first and "PREVIOUS ATTEMPT" not in user:
            text = "not json at all"
        else:
            text = json.dumps(self._draft(mid, user))
        stdout = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text,
                             "modelUsage": {self.model: {"outputTokens": 1}}, "usage": {"output_tokens": 1}})
        return {"argv": ["fake"], "raw_stdout": stdout, "elapsed_sec": 0.0}

    def _draft(self, mid, user):
        scen = re.findall(r"^- (F\d_[A-Z_]+) \[", user, re.M)
        absent = re.findall(r"^([A-D])  .*ABSENT", user, re.M)
        present = [x for x in "ABCD" if x not in absent]
        p0, p1 = present[0], present[1]
        segs = [{"n": i, "speaker": present[i % len(present)], "da_type": "inf",
                 "text": f"Segment {i} talking about the design."} for i in range(1, 15)]
        segs[1]["speaker"] = segs[2]["speaker"] = p0
        items, facts, tags = [], [], []
        owner_absent = absent[0] if absent else p1
        items.append({"n": 1, "description": "Check costs", "owner": None, "owner_unclear": True,
                      "status": "active", "previous_owners": [], "history": [], "evidence_segments": [3]})
        items.append({"n": 2, "description": "Draft spec", "owner": owner_absent, "status": "active",
                      "previous_owners": [], "history": [{"segment": 4, "event": "assigned", "by": p0, "owner": owner_absent}],
                      "evidence_segments": [4]})
        items.append({"n": 3, "description": "Research", "owner": p1, "status": "active", "previous_owners": [],
                      "history": [{"segment": 5, "event": "offered", "by": p0, "owner": p0},
                                  {"segment": 6, "event": "accepted", "by": p1, "owner": p1}],
                      "evidence_segments": [5, 6]})
        items.append({"n": 4, "description": "Order parts", "owner": p0, "status": "dropped", "previous_owners": [],
                      "history": [{"segment": 7, "event": "assigned", "by": p0, "owner": p0},
                                  {"segment": 8, "event": "dropped", "by": p0, "owner": None}],
                      "evidence_segments": [7, 8]})
        for t in ("decision", "plan_change", "absence", "action_item", "unagreed_proposal", "action_item_removed"):
            facts.append({"type": t, "fact": f"A {t} fact.", "agreed": False if t == "unagreed_proposal" else None,
                          "related_action_item": 4 if t == "action_item_removed" else None,
                          "refers_to_meeting": None, "evidence_segments": [9]})
        if not mid.endswith("a"):
            facts.append({"type": "reference_to_prior_meeting", "fact": "Refers back.", "agreed": None,
                          "related_action_item": None, "refers_to_meeting": mid[:-1] + "a", "evidence_segments": [10]})
        span = {"F1_OFF_TOPIC_TANGENT": list(range(1, 9)), "F1_SELF_CORRECTION": [2, 3],
                "F1_INTERLEAVED_TOPICS": [1, 3, 5, 7]}
        for s in scen:
            tags.append({"scenario_id": s, "segments": span.get(s, [4, 5, 6, 7, 8]), "note": "fake"})
        draft = {"segments": segs,
                 "summary": {"abstract": ["The team met."], "actions": [], "decisions": [], "problems": []},
                 "action_items": items, "qa_facts": facts, "edge_cases": tags}
        if mid.endswith("a"):
            draft["names"] = {x: f"Name{x}" for x in "ABCD"}
        return draft
