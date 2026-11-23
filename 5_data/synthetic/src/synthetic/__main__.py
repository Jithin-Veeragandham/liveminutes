"""CLI: python -m synthetic <command> [--config config/generation.yaml]"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .util import load_config, load_scenarios, read_json, resolve, write_json


def _cfg(args):
    return load_config(args.config)


def cmd_plan(args):
    from .plan import build_plan, coverage
    cfg = _cfg(args)
    _, scenarios = load_scenarios(cfg)
    plan = build_plan(cfg, scenarios)
    out = {"plan": plan, "coverage": coverage(plan)}
    print(json.dumps(out if args.full else out["coverage"], indent=2))


def _report(res):
    print(json.dumps({k: v for k, v in res.items() if k not in ("failed", "api_errors")}, indent=2))
    for f in res["failed"]:
        print(f"FAILED {f['meeting_id']}: {f['last_errors'][:3]}", file=sys.stderr)
    for e in res.get("api_errors", []):
        print(f"API ERROR {e['meeting_id']} (rerun generate to resume): {e['error'][:300]}", file=sys.stderr)
    return 1 if res["failed"] or res.get("api_errors") else 0


def cmd_generate(args):
    from .pipeline import run
    return _report(run(_cfg(args), allow_api=True, only_series=args.series, workers=args.workers))


def cmd_build(args):
    from .pipeline import CacheMiss, run
    try:
        return _report(run(_cfg(args), allow_api=False, out_dir=args.out))
    except CacheMiss as e:
        print(f"cache miss: {e}", file=sys.stderr)
        return 2


def cmd_validate(args):
    from .plan import build_plan
    from .validate import validate_meeting
    cfg = _cfg(args)
    _, scenarios = load_scenarios(cfg)
    by_id = {s["id"]: s for s in scenarios}
    plans = {m["meeting_id"]: m for s in build_plan(cfg, scenarios) for m in s["meetings"]}
    paths = [Path(p) for p in args.files] or sorted((Path(args.dir) if args.dir else resolve(cfg["paths"]["raw_dir"])).glob("meetings/*.json"))
    bad = 0
    for p in paths:
        m = read_json(p)
        mp = plans.get(m["meeting_id"]) if m["source"] == "synthetic" else None
        prior = None
        if mp:
            prior = [f"{m['series_id']}{c}" for c in "abcd"[: "abcd".index(m["meeting_letter"])]]
        errs = validate_meeting(m, by_id if m["source"] == "synthetic" else None, mp, prior)
        print(f"{'OK  ' if not errs else 'FAIL'} {m['meeting_id']}" + "".join(f"\n     - {e}" for e in errs[:10]))
        bad += bool(errs)
    print(f"{len(paths) - bad}/{len(paths)} valid")
    return 1 if bad else 0


def cmd_split(args):
    from .splits import assign
    cfg = _cfg(args)
    index = read_json(resolve(cfg["paths"]["raw_dir"]) / "index.json")
    sp = cfg["splits"]
    result = assign(index, sp["seed"], sp["ratios"])
    out = resolve(cfg["paths"]["splits_dir"]) / "synthetic_splits.json"
    write_json(out, result)
    print(json.dumps({s: len(v) for s, v in result["splits"].items()}), "->", out)
    if result["empty_splits"] and not args.allow_empty:
        print(f"ERROR: empty splits {result['empty_splits']} (need >= 3 series per feature)", file=sys.stderr)
        return 1
    return 0


def cmd_review_export(args):
    from .review import export
    cfg = _cfg(args)
    n = export(resolve(cfg["paths"]["raw_dir"]), resolve(cfg["paths"]["review_dir"]), args.overwrite)
    print(f"exported; {n} new review forms in {resolve(cfg['paths']['review_dir']) / 'forms'}")


def cmd_agreement(args):
    from .review import agreement
    cfg = _cfg(args)
    rd = resolve(cfg["paths"]["review_dir"])
    res = agreement(rd)
    write_json(rd / "agreement.json", res)
    print(json.dumps({k: v for k, v in res.items() if k != "disagreements"}, indent=2))
    print(f"{len(res['disagreements'])} disagreements -> {rd / 'agreement.json'}")


def cmd_review_apply(args):
    from .review import ReviewError, apply
    cfg = _cfg(args)
    try:
        man = apply(resolve(cfg["paths"]["raw_dir"]), resolve(cfg["paths"]["review_dir"]),
                    resolve(cfg["paths"]["reviewed_dir"]), args.release)
    except ReviewError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"release {args.release}: kept {len(man['kept'])}, excluded {len(man['excluded'])}")


def cmd_ami_convert(args):
    from .ami import convert_all
    from .validate import validate_meeting
    out = Path(args.out)
    n = bad = 0
    for m in convert_all(Path(args.ami_dir), args.meetings or None):
        errs = validate_meeting(m)
        if errs:
            bad += 1
            print(f"INVALID {m['meeting_id']}: {errs[:3]}", file=sys.stderr)
        write_json(out / f"{m['meeting_id']}.json", m)
        n += 1
    print(f"converted {n} AMI scenario meetings -> {out} ({bad} invalid)")
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m synthetic")
    ap.add_argument("--config", default="config/generation.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="show the deterministic meeting plan / scenario coverage")
    p.add_argument("--full", action="store_true")
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("generate", help="call the LLM on cache misses, then write the dataset")
    p.add_argument("--series", nargs="*", help="only these series IDs")
    p.add_argument("--workers", type=int, default=1, help="series generated in parallel (output is identical)")
    p.set_defaults(fn=cmd_generate)
    p = sub.add_parser("build", help="rebuild the dataset from cache with zero API calls")
    p.add_argument("--out", help="output dir (default paths.raw_dir)")
    p.set_defaults(fn=cmd_build)
    p = sub.add_parser("validate", help="validate meeting JSON files")
    p.add_argument("files", nargs="*")
    p.add_argument("--dir")
    p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("split", help="assign series to train/dev/test")
    p.add_argument("--allow-empty", action="store_true")
    p.set_defaults(fn=cmd_split)
    p = sub.add_parser("review-export")
    p.add_argument("--overwrite", action="store_true", help="also overwrite existing review forms")
    p.set_defaults(fn=cmd_review_export)
    p = sub.add_parser("agreement")
    p.set_defaults(fn=cmd_agreement)
    p = sub.add_parser("review-apply")
    p.add_argument("--release", required=True, help="e.g. v1")
    p.set_defaults(fn=cmd_review_apply)
    p = sub.add_parser("ami-convert")
    p.add_argument("--ami-dir", required=True)
    p.add_argument("--out", default=str(resolve("data/ami")))
    p.add_argument("--meetings", nargs="*")
    p.set_defaults(fn=cmd_ami_convert)
    args = ap.parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
