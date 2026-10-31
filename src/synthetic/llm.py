"""LLM client backends. Only called on a cache miss in `generate`.

claude_cli: Claude Code headless (`claude -p`). It uses the logged-in Claude
account (OAuth), so no API key is involved. Our system prompt replaces Claude
Code's. Tools, MCP servers, settings files, slash commands and session
persistence are all disabled, and the call runs in an empty temp directory so no
CLAUDE.md is picked up. The model receives only our system + user prompt.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


class LLMError(RuntimeError):
    pass


# Variables that mark a nested Claude Code session; removed so the child call is independent.
_NESTED_ENV = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_CHILD_SESSION",
               "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
               "CLAUDE_CODE_SSE_PORT", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_CODE_SESSION_ATTENDED")


def _real_executable(name: str) -> str:
    """Resolve the binary itself. On Windows, npm installs a claude.CMD shim, and
    cmd.exe truncates arguments at the first newline. That silently drops every flag
    after the multi-line system prompt, so the shim must never be used."""
    exe = shutil.which(name)
    if not exe:
        raise LLMError(f"executable {name!r} not found on PATH")
    if exe.lower().endswith((".cmd", ".bat")):
        real = Path(exe).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if not real.exists():
            raise LLMError(f"{exe} is a cmd shim and {real} was not found; set model.cli.executable to claude.exe")
        exe = str(real)
    return exe


class ClaudeCLIClient:
    def __init__(self, model_cfg: dict):
        self.cfg = model_cfg
        unsupported = {k: v for k, v in model_cfg.get("sampling", {}).items() if v is not None}
        if unsupported:
            raise LLMError(f"claude_cli backend cannot apply sampling params {unsupported}; set them to null")
        self.exe = _real_executable(model_cfg["cli"]["executable"])
        exe = self.exe
        ver = subprocess.run([exe, "--version"], capture_output=True, text=True, encoding="utf-8")
        self.cli_version = ver.stdout.strip()

    def argv(self, system: str) -> list[str]:
        return ["claude", "-p",
                "--model", self.cfg["name"],
                "--effort", self.cfg["cli"]["effort"],
                "--system-prompt", system,
                "--tools", "",
                "--strict-mcp-config",
                "--setting-sources", "",
                "--disable-slash-commands",
                "--no-session-persistence",
                "--output-format", "json"]

    def complete(self, system: str, user: str) -> dict:
        argv = self.argv(system)
        env = {k: v for k, v in os.environ.items() if k not in _NESTED_ENV}
        t0 = time.time()
        with tempfile.TemporaryDirectory(prefix="liveminutes-gen-") as cwd:
            proc = subprocess.run([self.exe, *argv[1:]], input=user, capture_output=True, text=True,
                                  encoding="utf-8", env=env, cwd=cwd, timeout=self.cfg["cli"]["timeout_sec"])
        elapsed = round(time.time() - t0, 1)
        def fail(msg):
            raise LLMError(msg, {"argv": argv, "stdout": proc.stdout, "stderr": proc.stderr,
                                 "returncode": proc.returncode, "elapsed_sec": elapsed})
        if proc.returncode != 0:
            fail(f"claude exited {proc.returncode}: {(proc.stderr or proc.stdout)[:2000]}")
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            fail(f"CLI output is not the JSON envelope (flags lost?): {proc.stdout[:300]}")
        if out.get("is_error") or out.get("subtype") != "success":
            fail(f"CLI reported error: {str(out)[:2000]}")
        models = sorted(out.get("modelUsage", {}))
        if models != [self.cfg["version"]]:
            fail(f"response came from {models}, expected [{self.cfg['version']}]")
        return {"argv": argv, "raw_stdout": proc.stdout, "elapsed_sec": elapsed, "cli_version": self.cli_version}


def make_client(model_cfg: dict):
    if model_cfg["backend"] == "claude_cli":
        return ClaudeCLIClient(model_cfg)
    raise LLMError(f"unknown backend {model_cfg['backend']!r}")


def response_text(raw_stdout: str) -> str:
    return json.loads(raw_stdout)["result"]


def response_model(raw_stdout: str) -> list[str]:
    return sorted(json.loads(raw_stdout).get("modelUsage", {}))
