# LiveMinutes synthetic meeting generator

Reproducible, LLM-generated synthetic meetings for the CS6170 LiveMinutes capstone. They cover
edge cases that AMI's role-play scenario meetings are too clean to contain. Every synthetic meeting
comes from an LLM call made by `python -m synthetic generate`. Nothing is hand-written.

AMI meetings and synthetic meetings share one JSON format (`schema/meeting.schema.json`), so the
same downstream code reads both.

## Layout

```
5_data/synthetic/
  config/          generation.yaml (full run), dryrun.yaml (1 series x 2 meetings)
  scenarios/       scenarios.yaml (the only source of edge-case specs)
  prompts/v1/      system.txt, user.txt, repair.txt (versioned templates)
  schema/          meeting.schema.json (canonical schema, shared with AMI)
  src/synthetic/   plan, prompts, llm, cache, assemble, validate, pipeline, splits, review, ami, CLI
  cache/           raw LLM requests + responses (COMMITTED; source of truth for rebuilds)
  data/raw/        dataset built from cache (meetings/, index.json, plan.json, validation_report.json, manifest.json)
  data/reviewed/   human-reviewed releases (v1/, v2/, ...), each immutable
  data/splits/     synthetic_splits.json (separate from the AMI split)
  review/          export/*.md (readable transcripts), forms/*.review.json, agreement.json
  samples/         committed dry run: samples/cache + samples/raw
  tests/           byte-identical rebuild test + unit tests
```

Additions to the requested layout: `schema/` holds the JSON schema (it is shared data, not code),
and `config/dryrun.yaml` writes the dry run into `samples/` with the same model and prompt settings.

## Setup

Requires Python 3.11+ and Claude Code (`claude`) signed in with the org account.

```bash
cd 5_data/synthetic                # its own venv: it needs PyYAML + jsonschema, not the repo's requirements.txt
python -m venv .venv
.venv/Scripts/activate            # Windows (Git Bash). macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"           # pinned: PyYAML 6.0.3, jsonschema 4.23.0, pytest 8.3.4
# full pinned environment: requirements.lock (pip install -r requirements.lock)
```

### Authentication (no API key)

`model.backend: claude_cli` sends each request through Claude Code headless mode (`claude -p`),
which authenticates with the logged-in Claude account. The request is isolated:

- `--system-prompt` replaces Claude Code's own system prompt with `prompts/v1/system.txt`
- `--tools ""`, `--strict-mcp-config`, `--setting-sources ""`, `--disable-slash-commands`: no tools, MCP servers, settings, or skills
- `--no-session-persistence`, and the call runs in an empty temp dir so no CLAUDE.md is read
- `--model claude-opus-5-5 --effort high --output-format json`

The pipeline rejects a response if the model reported in `modelUsage` is not the pinned `model.version`.

Limitation: Claude Code cannot set temperature, top_p, max_tokens, or a seed, and the Anthropic API
has no seed parameter at all. These keys stay in `config/generation.yaml` as `null` and are part of
the cache key. `generate` refuses to run if any is non-null on a backend that cannot apply it.
Reproducibility comes from the cache, not from sampling settings.

## Regenerate from scratch (calls the API)

```bash
python -m synthetic plan                     # scenario coverage of the plan (no API)
python -m synthetic plan --full              # every meeting: roles, absences, product, scenarios
python -m synthetic generate --workers 5     # API calls on cache misses only; writes cache/ and data/raw/
                                             # --workers runs series in parallel; output is identical for any value
python -m synthetic validate                 # re-check every built meeting
python -m synthetic split                    # data/splits/synthetic_splits.json
```

To regenerate truly from scratch, delete `cache/SY*` first. Otherwise `generate` resumes: cached
meetings are reused, and only missing ones are requested. An API error stops only its own
series (raw output kept in `cache/_failed_calls/`); rerun `generate` to resume. Use `generate --series SY0101 SY0102` to
limit a run.

## Rebuild from cache (zero API calls)

```bash
python -m synthetic build                    # data/raw/ from cache/; fails on any cache miss
python -m synthetic build --out /tmp/x       # build somewhere else, e.g. to diff
pytest                                       # includes the byte-identical rebuild tests
```

`build` never creates an LLM client. Running it twice produces byte-identical output. The tests
`tests/test_rebuild.py::test_build_twice_is_byte_identical` (stub LLM) and
`test_committed_dry_run_rebuilds_identically` (real cached dry run in `samples/`) check this.

## Dry run

```bash
python -m synthetic --config config/dryrun.yaml generate    # 2 meetings (SY0201a, SY0201b) -> samples/
python -m synthetic --config config/dryrun.yaml build
```

## How reproducibility works

1. **Plan** (`plan.py`): deterministic from `generation.seed`. Each series has its own RNG seeded
   by `(seed, series_id)`, so adding series never changes existing ones. Series IDs come in blocks
   per feature: F1 → `SY01xx`, F2 → `SY02xx`, F3 → `SY03xx`. Meeting IDs follow AMI's
   series-plus-letter pattern: `SY0101a` … `SY0101d`.
2. **Prompt** (`prompts.py`): the meeting plan plus the gold labels of the series' earlier meetings
   are rendered into the versioned templates.
3. **Cache key** (`cache.py`): sha256 of the scenario spec entries, template hashes, the whole
   `model:` config block, the seed, the fully rendered request (which carries the plan and the prior
   meetings), and the attempt number. Files are `cache/<series>/<meeting>.a<attempt>.<key16>.json`,
   with the raw CLI stdout stored verbatim. Entries are never overwritten.
4. **Assemble** (`assemble.py`): the model refers to segments by number. Code assigns segment IDs
   (`SY0101a.s0001`) and synthetic timestamps (word count / 2.6 words per second, plus a hashed pause),
   so timestamps are monotonic by construction.
5. **Validate** (`validate.py`): JSON schema check; speakers are listed participants; absent
   participants never speak; timestamps are monotonic; every segment reference exists; every gold
   owner is a listed participant (null only when `owner_unclear`); each planned edge case is tagged
   and passes its scenario checks (see `checks:` in `scenarios.yaml`); meetings b–d refer back to an
   earlier meeting.
6. **Retry**: on failure the next attempt (up to `max_attempts`) sends the validation errors back
   (`prompts/v1/repair.txt`). Every attempt is recorded in `data/raw/validation_report.json`. A meeting
   that fails every attempt is listed under `failed`, and later meetings of its series under `blocked`,
   in both the report and the manifest. The command exits non-zero. Nothing is dropped silently.
7. **Manifest** (`data/raw/manifest.json`): git commit and dirty flag (outputs and cache excluded),
   full config, pinned model plus the model IDs seen in responses, generation timestamps (first and
   last response time, taken from the cache so rebuilds stay identical), scenario spec hash, template
   hashes, schema hash, and a sha256 of every output file.

## Splits

`python -m synthetic split` assigns whole series to train/dev/test (ratios 0.6/0.2/0.2, seeded),
stratified by feature. With at least 3 series per feature, every split gets every feature. Because
each series covers all of its feature's scenarios, every split also contains every scenario. The
file is `data/splits/synthetic_splits.json`, kept apart from the AMI split.

## Human review

```bash
python -m synthetic review-export        # review/export/<id>.md + review/forms/<id>.review.json
# reviewers 1 and 2 fill reviewer_1 / reviewer_2 in each form: decision accept|reject|edit (+ edit, comment)
python -m synthetic agreement            # Cohen's kappa + percent agreement -> review/agreement.json
# reviewer 3 fills `resolution` on every disagreement listed in agreement.json
python -m synthetic review-apply --release v1   # -> data/reviewed/v1/ (cache/ and data/raw/ untouched)
```

Each form has one item for the meeting, the transcript, each summary section, each action item,
each Q&A fact, and each edge-case tag. Edits replace the gold value. Rejecting the `meeting` item
excludes the meeting from the release. `review-apply` refuses to run if any item is unresolved, if a
form was exported from a different raw build (hash check), or if the release already exists.
Edited meetings are re-validated before they are written.

## AMI converter

```bash
# needs the FULL manual annotations (ontologies/, extractive/), not fetch_data.py's smaller extract:
python -c "import zipfile; zipfile.ZipFile('../raw/ami/ami_public_manual_1.6.2.zip').extractall('../raw/ami/ami_public_manual_1.6.2')"
python -m synthetic ami-convert --ami-dir ../raw/ami/ami_public_manual_1.6.2 --out data/ami
```

Converts the 138 scenario meetings (ES/IS/TS) in `meetings.xml` to the same schema: participants
from `meetings.xml` (nxt_agent letter, role, global_name), segments from dialogue acts (words
joined, punctuation attached, vocal sounds and gaps skipped), abstractive summary sections, and
evidence segments from `extractive/*.summlink.xml`. AMI has no owner field on actions, so the owner
comes from the role named in the action sentence (174 of 292 actions); otherwise it is null with
`owner_unclear: true`. Status is `active`. All 138 converted meetings pass validation.
`data/ami/` is git-ignored because it is derived from the licensed corpus.

## Scenario spec

`scenarios/scenarios.yaml`: 13 scenarios (6 F1, 4 F2, 3 F3). Each has an ID, target feature,
description (inserted verbatim into the prompt), required gold labels, `needs_absent`, and
machine checks.
