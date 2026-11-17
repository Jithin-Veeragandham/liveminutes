"""Shared helpers: config loading, canonical JSON, hashing, deterministic file writes."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schema" / "meeting.schema.json"


def canonical(obj) -> str:
    """Stable JSON used for hashing: sorted keys, no whitespace, unicode kept."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def write_text(path: Path, text: str) -> None:
    """Write UTF-8 with LF newlines on every OS, so rebuilds are byte-identical."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_json(path: Path, obj) -> None:
    write_text(path, dumps(obj))


def read_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(path: str | Path) -> dict:
    """Load a YAML config, resolving `extends:` relative to the file."""
    path = Path(path)
    if not path.is_absolute():
        path = (ROOT / path) if not path.exists() else path.resolve()
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    parent = cfg.pop("extends", None)
    if parent:
        cfg = _merge(load_config(path.parent / parent), cfg)
    p = path.resolve()
    cfg["_config_file"] = (p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p.as_posix())
    return cfg


def resolve(rel: str) -> Path:
    return ROOT / rel


def load_scenarios(cfg: dict) -> tuple[dict, list[dict]]:
    with open(resolve(cfg["generation"]["scenarios_file"]), encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    ids = [s["id"] for s in spec["scenarios"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate scenario IDs in scenarios file")
    return spec, spec["scenarios"]


def git_state() -> dict:
    """Commit and whether tracked inputs (not outputs/cache) differ from it."""
    def run(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    head = run("rev-parse", "HEAD")
    if head.returncode != 0:
        return {"commit": None, "dirty": None}
    status = run("status", "--porcelain", "--", ".",
                 ":(exclude)data", ":(exclude)samples", ":(exclude)review", ":(exclude)cache")
    return {"commit": head.stdout.strip(), "dirty": bool(status.stdout.strip())}
