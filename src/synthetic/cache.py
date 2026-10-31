"""Content-addressed cache of raw LLM requests and responses.

Key = sha256 of the canonical JSON of:
  scenarios   the full scenarios.yaml entries used by the meeting
  templates   sha256 of every prompt template file of the prompt version
  model       the whole `model:` config block (provider, backend, name, version, effort, sampling)
  seed        generation seed
  request     the fully rendered system + user prompt (this carries the meeting plan
              and the prior meetings of the series, so changing meeting a re-keys b..d)
  attempt     retry number (0-based)

File: <cache_dir>/<series_id>/<meeting_id>.a<attempt>.<key[:16]>.json
Entries are written once and never modified.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .util import canonical, read_json, sha256_text, write_json


def key_payload(scenarios: list[dict], template_hashes: dict, model_cfg: dict, seed: int,
                request: dict, attempt: int) -> dict:
    return {"scenarios": scenarios, "templates": template_hashes, "model": model_cfg,
            "seed": seed, "request": request, "attempt": attempt}


def cache_key(payload: dict) -> str:
    return sha256_text(canonical(payload))


def entry_path(cache_dir: Path, meeting_id: str, attempt: int, key: str) -> Path:
    return Path(cache_dir) / meeting_id[:-1] / f"{meeting_id}.a{attempt}.{key[:16]}.json"


def load(cache_dir: Path, meeting_id: str, attempt: int, key: str) -> dict | None:
    p = entry_path(cache_dir, meeting_id, attempt, key)
    if not p.exists():
        return None
    entry = read_json(p)
    if entry["key"] != key:
        raise RuntimeError(f"cache key collision/corruption at {p}")
    return entry


def store(cache_dir: Path, meeting_id: str, attempt: int, key: str, payload: dict, result: dict) -> dict:
    entry = {
        "key": key,
        "meeting_id": meeting_id,
        "attempt": attempt,
        "received_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "key_inputs": {k: payload[k] for k in ("scenarios", "templates", "model", "seed", "attempt")},
        "request": {"argv": result["argv"], "cli_version": result.get("cli_version"), **payload["request"]},
        "response": {"raw_stdout": result["raw_stdout"], "elapsed_sec": result["elapsed_sec"]},
    }
    p = entry_path(cache_dir, meeting_id, attempt, key)
    if p.exists():
        raise RuntimeError(f"refusing to overwrite cache entry {p}")
    write_json(p, entry)
    return entry
