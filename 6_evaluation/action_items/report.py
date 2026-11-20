"""Markdown tables from the results log: the latest logged run of each system on AMI eval and synthetic test.

    .venv/bin/python 6_evaluation/action_items/report.py              # main table + breakdowns
    .venv/bin/python 6_evaluation/action_items/report.py --split dev  # the same for dev runs
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "3_action_items"))
import settings  # noqa: E402


def latest(split_by_data):
    runs = {}
    with open(settings.RESULTS_LOG) as f:
        for line in f:
            r = json.loads(line)
            if split_by_data.get(r["data"]) == r["split"]:
                runs[(r["system"], r["data"])] = r   # later lines win
    return runs


def cell(m, key, ci=True):
    v = m.get(key)
    if v is None:
        return "n/a"
    c = m.get(key + "_ci")
    return f"{v:.2f} [{c[0]:.2f}, {c[1]:.2f}]" if ci and c else f"{v:.2f}"


def main_table(runs):
    rows = ["| System | Data | Meetings | Gold | Predicted | Matched | Precision | Recall | F1 | Owner acc. (n) | Due acc. (n) |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for (system, source), r in sorted(runs.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        m = r["metrics"]
        rows.append(f"| {system} | {source} {r['split']} | {m['n']} | {m['gold']} | {m['pred']} | {m['matched']} | "
                    f"{cell(m, 'precision')} | {cell(m, 'recall')} | **{cell(m, 'f1')}** | "
                    f"{cell(m, 'owner_acc')} ({m['owner_n']}) | {cell(m, 'due_acc')} ({m['due_n']}) |")
    return "\n".join(rows)


def breakdown_tables(runs, source):
    systems = sorted({s for s, d in runs if d == source})
    if not systems:
        return ""
    first = runs[(systems[0], source)]
    out = []
    for group, values in first["breakdowns"].items():
        head = "| " + group.replace("_", " ") + " | n | Gold | " + " | ".join(f"{s} F1 | {s} owner" for s in systems) + " |"
        out += [head, "|" + "---|" * (3 + 2 * len(systems))]
        for value, s0 in values.items():
            small = " (too small)" if s0["too_small"] else ""
            cells = []
            for s in systems:
                g = runs[(s, source)]["breakdowns"][group][value]
                if g["gold"] == 0:   # nothing to find here: what counts is how many items were (wrongly) predicted
                    cells += [f"no gold; {g['pred']} predicted", "n/a"]
                else:
                    cells += [cell(g, "f1"), cell(g, "owner_acc")]
            out.append(f"| {value}{small} | {s0['n']} | {s0['gold']} | " + " | ".join(cells) + " |")
        out.append("")
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--split", default="final", choices=["final", "dev", "smoke"])
    a = p.parse_args()
    split_by_data = {"final": {"ami": "eval", "synthetic": "test"}, "dev": {"ami": "dev", "synthetic": "dev"},
                     "smoke": {"ami": "smoke", "synthetic": "smoke"}}[a.split]
    runs = latest(split_by_data)
    print(main_table(runs))
    for source in ("ami", "synthetic"):
        print(f"\n**{source} breakdowns** (F1 and owner accuracy, 95% bootstrap intervals over meetings or instances)\n")
        print(breakdown_tables(runs, source))
    print("Runs: " + ", ".join(f"{r['run_id']} (commit {r['commit'][:7]})" for r in runs.values()))


if __name__ == "__main__":
    main()
