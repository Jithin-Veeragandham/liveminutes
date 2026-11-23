"""Disaggregated scores: the same metrics, averaged separately for each group of meetings.

An overall average can hide a model that does well on one kind of meeting and badly on another. Each dataset
puts every meeting in groups (datasets.py): for AMI the speakers' gender mix and native-language mix, for the
synthetic data the feature tier and the edge-case scenarios. Here we average every metric within each group,
and report the gap between the best and the worst group.

Read small groups with care: n is the number of meetings, and AMI meetings come in series of 4 with the same
4 people, so n_series (the number of different groups of people) is the real sample size.
"""
import numpy as np

SMALL = 2   # groups with fewer than this many series are flagged: one group of people, not a trend


# The values of one group attribute for one meeting, as a list (a synthetic meeting has two scenarios).
def values(groups, attribute):
    v = groups.get(attribute)
    return v if isinstance(v, list) else [v]


# per_meeting: {meeting id: {metric: score}}; groups: {meeting id: {attribute: value or [values]}};
# attributes: which group attributes to break down by.
# Returns {attribute: {"groups": {value: {n, n_series, meetings, small, mean: {metric: score}}},
#                      "gap": {metric: best - worst group}, "worst": {metric: group}}}.
def by_group(per_meeting, groups, attributes):
    metrics = list(next(iter(per_meeting.values())))
    out = {}
    for attribute in attributes:
        members = {}
        for m in per_meeting:
            for v in values(groups[m], attribute):
                members.setdefault(str(v), []).append(m)
        table = {}
        for v, ms in sorted(members.items()):
            n_series = len({m[:-1] for m in ms})   # ES2002a -> ES2002, SY0101a -> SY0101
            table[v] = {"n": len(ms), "n_series": n_series, "small": n_series < SMALL, "meetings": ms,
                        "mean": {k: round(float(np.mean([per_meeting[m][k] for m in ms])), 4) for k in metrics}}
        gap, worst = {}, {}
        if len(table) > 1:
            for k in metrics:
                means = {v: t["mean"][k] for v, t in table.items()}
                worst[k] = min(means, key=means.get)
                gap[k] = round(max(means.values()) - min(means.values()), 4)
        out[attribute] = {"groups": table, "gap": gap, "worst": worst}
    return out


# Per-meeting scores averaged over seeds: [{meeting: {metric: score}}, ...] -> {meeting: {metric: mean}}.
def mean_over_seeds(per_seed_per_meeting):
    first = per_seed_per_meeting[0]
    return {m: {k: float(np.mean([run[m][k] for run in per_seed_per_meeting])) for k in first[m]} for m in first}


# Plain-text table of one breakdown, for the terminal.
def format_table(attribute, result, metrics):
    rows = [f"  by {attribute}:"]
    width = max([len(v) for v in result["groups"]] + [5])
    rows.append(f"    {'group':{width}}  {'n':>3} {'ser':>3}  " + "  ".join(f"{k:>9}" for k in metrics))
    for v, t in result["groups"].items():
        flag = "  (1 series: indicative only)" if t["small"] else ""
        rows.append(f"    {v:{width}}  {t['n']:>3} {t['n_series']:>3}  "
                    + "  ".join(f"{t['mean'][k]:>9.4f}" for k in metrics) + flag)
    if result["gap"]:
        rows.append(f"    {'gap':{width}}  {'':>3} {'':>3}  " + "  ".join(f"{result['gap'][k]:>9.4f}" for k in metrics))
    return "\n".join(rows)
