"""`generate` and `build`.

Both replay the plan meeting by meeting, attempt by attempt, against the cache:
  generate  calls the LLM on a cache miss, stores the raw request/response, continues
  build     never calls the LLM; a cache miss is reported as an error

The rest is shared and deterministic: parse, assemble, validate, retry decision,
and output. So `build` reproduces exactly what `generate` decided, and running
`build` twice gives byte-identical files.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
import sys
from pathlib import Path

from . import cache
from .assemble import DraftError, assemble, parse_draft
from .llm import LLMError, make_client, response_model, response_text
from .plan import build_plan, coverage
from .prompts import load_templates, render, template_hashes
from .util import ROOT, canonical, git_state, load_scenarios, resolve, sha256_file, write_json
from .validate import validate_meeting


class CacheMiss(RuntimeError):
    pass


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _append_log(cache_dir: Path, record: dict) -> None:
    p = cache_dir / "generation_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="\n") as f:
        f.write(canonical(record) + "\n")


def run(cfg: dict, allow_api: bool, out_dir: Path | None = None, only_series: list[str] | None = None) -> dict:
    gen, model_cfg = cfg["generation"], cfg["model"]
    cache_dir = resolve(cfg["paths"]["cache_dir"])
    out_dir = Path(out_dir) if out_dir else resolve(cfg["paths"]["raw_dir"])
    spec, scenarios = load_scenarios(cfg)
    by_id = {s["id"]: s for s in scenarios}
    templates = load_templates(gen["prompt_version"])
    t_hashes = template_hashes(templates)
    plan = build_plan(cfg, scenarios)
    client = None
    max_attempts = gen["max_attempts"]

    meetings, attempts_log, failed, blocked = [], [], [], []
    received, models_seen, api_calls = [], set(), 0

    for series in plan:
        if only_series and series["series_id"] not in only_series:
            continue
        prior: list[dict] = []
        for mplan in series["meetings"]:
            mid = mplan["meeting_id"]
            sc_entries = [by_id[s] for s in mplan["scenario_ids"]]
            errors: list[str] = []
            accepted = None
            for attempt in range(max_attempts):
                request = render(mplan, by_id, prior, templates, attempt, max_attempts, errors)
                payload = cache.key_payload(sc_entries, t_hashes, model_cfg, gen["seed"], request, attempt)
                key = cache.cache_key(payload)
                entry = cache.load(cache_dir, mid, attempt, key)
                if entry is None:
                    if not allow_api:
                        raise CacheMiss(f"{mid} attempt {attempt}: no cache entry {key[:16]} "
                                        f"(run `generate` first; build makes no API calls)")
                    client = client or make_client(model_cfg)
                    _log(f"[api] {mid} attempt {attempt} -> {model_cfg['name']} ...")
                    try:
                        result = client.complete(request["system"], request["user"])
                    except LLMError as e:
                        # Keep the raw output of failed calls; they are not cache entries.
                        if len(e.args) > 1:
                            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                            write_json(cache_dir / "_failed_calls" / f"{mid}.a{attempt}.{stamp}.json",
                                       {"key": key, "error": e.args[0], **e.args[1]})
                        raise
                    entry = cache.store(cache_dir, mid, attempt, key, payload, result)
                    api_calls += 1
                    _append_log(cache_dir, {"meeting_id": mid, "attempt": attempt, "key": key,
                                            "received_at": entry["received_at"],
                                            "elapsed_sec": result["elapsed_sec"],
                                            "usage": json.loads(result["raw_stdout"]).get("usage", {}).get("output_tokens")})
                else:
                    _log(f"[cache] {mid} attempt {attempt}")
                received.append(entry["received_at"])
                models_seen.update(response_model(entry["response"]["raw_stdout"]))
                provenance = {"generator": "liveminutes-synthetic", "cache_key": key, "attempt": attempt,
                              "model": model_cfg["name"], "prompt_version": gen["prompt_version"],
                              "scenario_spec_version": spec.get("spec_version")}
                try:
                    draft = parse_draft(response_text(entry["response"]["raw_stdout"]))
                    meeting, errors = assemble(draft, mplan, prior, provenance)
                    errors = errors + validate_meeting(meeting, by_id, mplan, [p["meeting_id"] for p in prior])
                except DraftError as e:
                    errors = [str(e)]
                attempts_log.append({"meeting_id": mid, "attempt": attempt, "cache_key": key,
                                     "valid": not errors, "errors": errors})
                if not errors:
                    accepted = meeting
                    break
                _log(f"[invalid] {mid} attempt {attempt}: {len(errors)} errors; first: {errors[0]}")
            if accepted is None:
                failed.append({"meeting_id": mid, "attempts": max_attempts, "last_errors": errors})
                rest = [m["meeting_id"] for m in series["meetings"]]
                blocked += rest[rest.index(mid) + 1:]
                _log(f"[FAILED] {mid} after {max_attempts} attempts; later meetings in {series['series_id']} blocked")
                break
            meetings.append(accepted)
            prior.append(accepted)

    return _write_outputs(cfg, out_dir, plan, meetings, attempts_log, failed, blocked,
                          received, models_seen, spec, t_hashes, api_calls)


def _write_outputs(cfg, out_dir, plan, meetings, attempts_log, failed, blocked,
                   received, models_seen, spec, t_hashes, api_calls) -> dict:
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    mdir = out_dir / "meetings"
    feature_of = {s["series_id"]: s["feature"] for s in plan}
    index = []
    for m in meetings:
        rel = f"meetings/{m['meeting_id']}.json"
        write_json(out_dir / rel, m)
        index.append({"meeting_id": m["meeting_id"], "series_id": m["series_id"],
                      "feature": feature_of[m["series_id"]],
                      "scenario_ids": m["provenance"]["planned_scenarios"],
                      "n_segments": len(m["segments"]), "file": rel})
    write_json(out_dir / "index.json", index)
    write_json(out_dir / "plan.json", {"plan": plan, "coverage": coverage(plan)})
    write_json(out_dir / "validation_report.json", {
        "attempts": attempts_log,
        "failed": failed,
        "blocked": blocked,
        "summary": {"planned": sum(len(s["meetings"]) for s in plan), "built": len(meetings),
                    "failed": len(failed), "blocked": len(blocked),
                    "retried": sorted({a["meeting_id"] for a in attempts_log if a["attempt"] > 0})},
    })
    files = {p.relative_to(out_dir).as_posix(): sha256_file(p)
             for p in sorted(out_dir.rglob("*")) if p.is_file()}
    cfg_public = {k: v for k, v in cfg.items() if not k.startswith("_")}
    manifest = {
        "dataset": "liveminutes-synthetic",
        "schema_version": "1.0",
        "git": git_state(),
        "config_file": cfg.get("_config_file"),
        "config": cfg_public,
        "model": {"provider": cfg["model"]["provider"], "name": cfg["model"]["name"],
                  "version_pinned": cfg["model"]["version"], "versions_in_responses": sorted(models_seen)},
        # Taken from the cache entries, not the wall clock, so rebuilds stay byte-identical.
        "generation_timestamp": {"first_response": min(received) if received else None,
                                 "last_response": max(received) if received else None},
        "scenario_spec": {"version": spec.get("spec_version"),
                          "sha256": sha256_file(resolve(cfg["generation"]["scenarios_file"]))},
        "prompt_templates": t_hashes,
        "schema_sha256": sha256_file(ROOT / "schema" / "meeting.schema.json"),
        "counts": {"meetings": len(meetings), "series": len({m["series_id"] for m in meetings}),
                   "failed": len(failed), "blocked": len(blocked)},
        "failed_meetings": [f["meeting_id"] for f in failed] + blocked,
        "files": files,
    }
    write_json(out_dir / "manifest.json", manifest)
    return {"out_dir": str(out_dir), "meetings": len(meetings), "failed": failed,
            "blocked": blocked, "api_calls": api_calls}
