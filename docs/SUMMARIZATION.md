# 2. Summarization (Feature 1): full reference

Feature 1: summarize a meeting. The short version (quick start, results, where things are recorded) is in the [README](../README.md). This file has the details: seeds, the step-by-step flow, every file, the split and leakage.

- **Data:** AMI meeting corpus (real meetings + human-written summaries, free, CC BY 4.0)
- **Baseline:** TextRank. Picks the most important lines from the transcript. No training, CPU, under 1 s per meeting.
- **Deep model:** BART (`philschmid/bart-large-cnn-samsum`). Writes new sentences. Not trained on AMI. About 45 s per meeting on CPU.
- **LLM:** rolling notes from a hosted LLM (default: Claude Sonnet 5.5, provider-agnostic). One call per ~30 s of meeting, at most one call every 30 s, each given the notes so far; returns a summary **and action items with owners**. See [2.12](#212-llm-rolling-notes-summary--action-items).
- **Score:** ROUGE (word overlap with the human summary). BERTScore (meaning overlap) for BART.
- **Datasets:** every run scores **both** AMI and the synthetic edge-case meetings in `5_data/synthetic`, one after the other, each logged as its own run. `--dataset ami` or `--dataset synthetic` runs just one.
- **Modes:** every model runs **recursively** by default (summary so far + next chunk -> new summary, like the live bot); `--mode full` gives it the whole transcript at once.
- **Scores by group:** every run also reports the metrics per group of meetings (AMI: speakers' gender mix and non-native share; synthetic: feature tier and edge-case scenario). See [2.13](#213-synthetic-data-recursive-mode-and-scores-by-group).

## 2.1 Commands

Setup and the one smoke-test command are in the [README quick start](../README.md#4-quick-start-fresh-clone-to-the-smoke-test-number). Everything below runs from the repo root after that.

**Other commands** (from the repo root)

| Want to... | Run |
|---|---|
| Baseline on the 20 test meetings | `.venv/bin/python 2_summarization/run.py --model textrank --split eval` |
| Baseline on the 20 dev meetings | `.venv/bin/python 2_summarization/run.py --model textrank --split dev` |
| Use 5 seeds instead of 20 | add `--seeds 5` |
| Use exactly one seed (e.g. 7) | add `--seed 7` |
| BART | `.venv/bin/python 2_summarization/run.py --model bart --split smoke --seeds 3 --bertscore` (~3 GB download once) |
| LLM rolling notes on AMI (1 seed by default, needs `ANTHROPIC_API_KEY`) | `.venv/bin/python 2_summarization/run.py --model llm --split smoke` |
| LLM notes for a live meeting | `.venv/bin/python 2_summarization/llm_summarizer.py 1_ingestion/sessions/<bot_id>/transcript.txt` |
| LLM offline tests (no key, no cost) | `.venv/bin/python -m unittest discover -s 2_summarization/tests` |
| Only check the data | `.venv/bin/python 5_data/ami/fetch_data.py --verify` |
| Only check the split | `.venv/bin/python 5_data/ami/split.py --check` |
| Run without saving a result | add `--no-log` |
| Only one dataset (default: both, AMI then synthetic) | add `--dataset ami` or `--dataset synthetic` |
| Whole transcript at once instead of recursive | add `--mode full` |

## 2.2 Seeds

The number of seeds is set **at the top of `2_summarization/run.py`**, not in a settings file:

```python
N_SEEDS = 20      # how many seeds a run uses
FIRST_SEED = 42   # seeds are 42, 43, ..., 61
```

- Change it there, or per run on the command line: `--seeds N` (N seeds) or `--seed S` (one seed, S).
- For every seed, `set_seeds()` fixes Python `random`, NumPy and torch. `PYTHONHASHSEED` is set to the first seed (the script restarts itself once, since it only works at startup).
- The log keeps each seed's score, plus the mean and standard deviation across seeds.
- Every run record also has a `seed_variation` section that answers "what changed between seeds, and why": a one-line `verdict`; `why` (what the seed can and cannot change for this model); for each metric its std, min and max with the seed that gave them, and the range; and `per_meeting`, listing every meeting whose summary text differed between seeds (how many distinct summaries, which seeds differed from the first, and that meeting's std and range per metric). The same summary is printed at the end of the run.
- TextRank has no randomness, so all 20 seeds give the same summary (`identical_across_seeds: true`, std 0). Seeds start to matter with BART and the LLM.

## 2.3 How it works (the flow)

`main()` at the bottom of `2_summarization/run.py` does these steps in order, each one marked with a comment:

```
0. seeds          -> pick the seeds (top of the file or --seeds / --seed), fix PYTHONHASHSEED
1. check data     -> datasets.py: AMI via fetch_data.py (download if missing, hash vs MANIFEST.json);
                     synthetic: every file's sha256 vs its data/raw/manifest.json
2. load split     -> AMI: split.py (no person in two splits); synthetic: data/splits/synthetic_splits.json
3. build model    -> TextRank (or BART, or the LLM), in recursive or full mode
4. run            -> for each seed: set seeds, summarize each meeting, score it (metrics.py)
5. average        -> mean and std over seeds, and seed_variation (what changed between seeds, and why)
6. by group       -> disaggregate.py: the same metrics per group of meetings, and the gap between groups
7. save           -> one line in 6_evaluation/summarization/results/results_log.jsonl
```

## 2.4 What each file does

| File | What it does |
|---|---|
| `2_summarization/run.py` | **Start here.** The one command. Seeds at the top. Runs the 7 steps above. |
| `2_summarization/datasets.py` | AMI and synthetic meetings in one shape (lines, reference, groups), their splits and data checks. |
| `2_summarization/chunks.py` | Cuts a meeting into ~30 s chunks: shared by every model's recursive mode. |
| `2_summarization/settings.py` | Model settings: TextRank, BART and BERTScore versions. |
| `2_summarization/textrank_baseline.py` | The baseline. Full: one ranking. Recursive: re-ranks kept lines + each new chunk, keeping 1.5x the word budget, trimmed at the end. |
| `2_summarization/bart_summarizer.py` | The deep model. Full: ~60 s windows, then a summary of summaries. Recursive: summary so far + next window -> new summary. |
| `2_summarization/llm_summarizer.py` | The LLM row: rolling summary + action items, one call per chunk with the notes so far. Also a command for live transcripts. |
| `2_summarization/llm_providers.py` | Provider-agnostic LLM access (`generate_json`). Today: Anthropic. Add a class here for another provider. |
| `2_summarization/tests/` | Offline tests for the LLM code (a fake provider, no API calls). |
| `5_data/ami/ami_settings.py` | Where the AMI data lives, and the 3 smoke-test meetings. |
| `5_data/ami/fetch_data.py` | Downloads AMI and checks it matches the expected hash. |
| `5_data/ami/split.py` | Decides which meetings go to train / dev / eval. Checks no person is in two splits. |
| `5_data/ami/ami.py` | Reads AMI's XML files into transcript lines (speaker, time, text) and the human summary. |
| `5_data/ami/MANIFEST.json` | Where the data came from, when, and its hashes; also the split's paths and hashes (`split`). |
| `5_data/ami/LICENSE_AMI.txt` | AMI licence (CC BY 4.0). |
| `5_data/ami/ami_scenario_split.json` | The saved split. |
| `5_data/raw/ami/` | Downloaded data. **Not committed** (gitignored). |
| `6_evaluation/summarization/metrics.py` | ROUGE-1, ROUGE-2, ROUGE-L and BERTScore. |
| `6_evaluation/summarization/disaggregate.py` | Scores per group of meetings, the gap between groups, small-group flags. |
| `6_evaluation/summarization/results/results_log.jsonl` | One line per run: scores + everything needed to repeat it. |
| `6_evaluation/summarization/results/runs/<run_id>.json` | Same, plus every summary the model wrote, per seed and meeting. |

**Environment files are at the repo root:** one `requirements.txt` (section 1 ingestion, section 2 summarization baseline, section 3 BART + BERTScore, section 4 tests, section 5 the Anthropic SDK for the LLM; every summarization package pinned) and `.python-version` (3.12). The ingestion Docker image installs only section 1, so torch (~2 GB) stays out of it.

## 2.5 Libraries we used

| Part | Library | Version |
|---|---|---|
| Baseline model (TextRank) | `summa` | 1.2.0 |
| Deep model (BART) | `transformers` + `torch` | 5.20.2 / 2.14.1 |
| ROUGE-1, ROUGE-2, ROUGE-L | `rouge-score` (Google's package, stemming on) | 0.1.2 |
| BERTScore | `bert-score` (model: `roberta-large`, pinned) | 0.3.13 |
| LLM (default provider) | `anthropic` (model `claude-sonnet-5-5`) | 1.17.0 |

All scores are F-measures from 0 to 1: our summary compared with AMI's human summary.

## 2.6 Results

The numbers are in `6_evaluation/summarization/results/results_log.jsonl`. **TextRank baseline, 20 seeds** (logged before `--mode` existed: these are `full` mode, which `--mode full` reproduces exactly):

| Split | ROUGE-1 | ROUGE-2 | ROUGE-L | Std over seeds | Time per meeting |
|---|---|---|---|---|---|
| smoke (3 meetings) | 0.260 | 0.040 | 0.150 | 0 | 0.05 s |
| **eval (20 test meetings)** | **0.238** | **0.027** | **0.129** | 0 | 0.2 s |

- **Compared with the paper:** published TextRank ROUGE-1 on AMI is 35.25 ([Zhu et al. 2020](https://aclanthology.org/2020.findings-emnlp.19/), Table 2); ours is 23.8. Likely reasons, not checked yet: different summary length and a different set of test meetings.
- **BART:** not run yet.

Each log line has (what the walkthrough checks): the scores (mean, std, per seed), **git commit**, **config + config hash**, **data hash**, split-file hash, meeting list, seeds, Python version, package versions and hardware.

## 2.7 Reproducibility

| Check | Where |
|---|---|
| **Environment** | `requirements.txt` at the repo root (every package pinned), `.python-version` (3.12). Each run logs Python, packages, hardware. |
| **Seeds** | Top of `2_summarization/run.py`, applied every run (see [2.2](#22-seeds)). |
| **Data version** | `5_data/ami/MANIFEST.json`: URL, retrieval date, zip SHA-256, `data_hash`. Checked every run; the run stops if it doesn't match. Delete `5_data/raw/ami/` and run again to get the same bytes back. |
| **Split** | `5_data/ami/split.py`, look for `THE SPLIT IS MADE HERE`. Saved in `ami_scenario_split.json`; its path, SHA-256 and one SHA-256 per train/dev/eval list are in `MANIFEST.json` under `split`. Checked every run against both. `python 5_data/ami/split.py` rebuilds the file and updates the manifest entry. |
| **Results log** | Every number is saved with its commit, config and data hash. |

## 2.8 The split

AMI meetings come in groups of 4 (a, b, c, d) by the same 4 people. We split **by group**, so the same people never appear in both training and testing. This is AMI's official scenario-only split.

| Split | Meetings | Used for |
|---|---|---|
| train | 98 | Only to set the summary length (160 words) |
| dev | 20 | Trying things out |
| eval | 20 | Final test numbers |
| smoke | 3 (from dev) | Quick check that everything works |

- No randomness in the split, so no split seed is needed.
- Train has 98, not 100, because 2 meetings don't exist in AMI.

## 2.9 Leakage: how we keep test data out

| Risk | How we prevent it |
|---|---|
| Same person in train and test | Split by group. `split.py` checks this every run and stops if it fails. |
| Tuning on test meetings | The only setting tuned is summary length, taken from **train**. Smoke uses dev, not eval. |
| Human summary leaking into input | The model only sees the transcript. The human summary is only used for scoring. |
| Model already saw AMI | TextRank has no training. BART was trained on news + chat, not AMI. |
| Duplicate meetings | `split.py` stops if a meeting is in two splits. |

## 2.10 Why TextRank and ROUGE

- **TextRank:** no training, fast, and it only copies real lines, so it can't make things up. A published AMI score exists to compare against.
- **ROUGE:** the standard metric in AMI papers, so our numbers can be compared.
- **BERTScore:** gives credit for paraphrases, which is fair to BART since it rewrites sentences.

## 2.11 Who explains what (walkthrough)

| Member | Part | Files to know |
|---|---|---|
| 1 | Environment + seeds | `requirements.txt`, `.python-version`, top of `run.py` |
| 2 | Data + model version, split, leakage | `5_data/ami/` (all files), `settings.py` |
| 3 | Baseline run, metric, results log | `run.py`, `textrank_baseline.py`, `metrics.py`, `results_log.jsonl` |

## 2.12 LLM rolling notes (summary + action items)

**What it does.** `llm_summarizer.py` cuts the meeting into chunks of about 30 seconds (`LLM["chunk_seconds"]`). For each chunk it sends the LLM two things: the notes so far (empty for the first chunk) and the new part of the transcript. The LLM returns updated notes for the whole meeting so far, and those go into the next call. That's how notes stay current during a live meeting: every call sees only the new part plus a short memory of the rest, so the cost per call stays the same however long the meeting runs.

**Structured reply.** The request carries a JSON schema (`SCHEMA`), and the provider forces the reply to match it (Anthropic: structured outputs):

```json
{"summary": "The team agreed on a rubber case ...",
 "action_items": [{"task": "Send the cost estimate to the team", "owner": "Priya", "due": "by Friday"},
                  {"task": "Check battery suppliers", "owner": "", "due": ""}]}
```

- **`owner`:** the name as the transcript gives it: a real name in live meetings, or a role such as "Project Manager" on AMI. It's `""` when nobody is clearly responsible, or when the speaker is "Unknown speaker".
- **`due`:** the deadline as it was said, or `""`.

**Provider-agnostic.** The summarizer only calls `provider.generate_json(system, user, schema, max_tokens)`. `llm_providers.py` has two providers: `AnthropicProvider` (official `anthropic` SDK, needs `ANTHROPIC_API_KEY` or an `ant auth login`) and `GeminiProvider` (Gemini REST API, standard library only, needs `GEMINI_API_KEY`). To use Gemini, put `LIVEMINUTES_LLM_PROVIDER=gemini` and `LIVEMINUTES_LLM_MODEL=gemini-3.5-flash-lite` in `.env`. To use another LLM, add a class with the same method to `PROVIDERS` and set `LIVEMINUTES_LLM_PROVIDER` / `LIVEMINUTES_LLM_MODEL` in `.env`, or change `settings.LLM`.

**Settings (`settings.LLM`):**

| Setting | Value | Why |
|---|---|---|
| `model` | `claude-sonnet-5-5` | Fast and strong enough for notes. |
| `chunk_seconds` | 30 | Notes update every ~30 s of meeting. |
| `min_seconds_between_calls` | 30 | Real-time pace: never more than one call per 30 s, far under free-tier rate limits. A 30-min recording takes ~30 min to process. `--no-wait` (both commands) turns it off. |
| `summary_words` | train average (160) on AMI; `LLM_LIVE_SUMMARY_WORDS` = 160 for live meetings | Same length budget as TextRank, so ROUGE comparisons are fair. |
| `effort` | `medium` | How much the model thinks before answering (Sonnet's default is `high`). |
| `fallbacks` | `default` | If Claude's safety filter declines a request, the API reruns it on a fallback model. `None` turns this off. |

**On AMI** (`run.py --model llm`): ROUGE and BERTScore score the summary, the same as the other models. Action items are saved per meeting in `runs/<run_id>.json` (`per_seed_runs.<seed>.action_items`) but not scored yet: AMI's `<actions>` summaries could be used for that later. The log line also gets `llm_usage` (API calls, input and output tokens, and which model answered).

**Seeds.** A hosted LLM can't be seeded, so two runs can give different text. That's why a run uses **1 seed unless you pass `--seeds N`**. Each extra seed repeats every API call, and its spread measures the model's run-to-run variation.

**Cost and time.** One call per chunk. An AMI meeting (~20-40 min) is ~40-80 calls, so with the 30 s pacing the smoke split takes over an hour and the eval split many hours: use `--no-wait` there (the Gemini provider retries if it gets rate-limited). `run.py` prints the estimate before it starts.

**In the meeting bot.** The bridge runs this same code live: see [INGESTION.md](INGESTION.md), "Live notes". The live page shows the summary and action items as they update.

**Key.** `ANTHROPIC_API_KEY` (Claude) or `GEMINI_API_KEY` (Gemini) goes in the repo's `.env` (see `.env.example`). Without it, both commands stop with a message saying so.

**First result (not logged, uncommitted code, 5-minute chunks at the time):** Gemini `gemini-3.5-flash-lite`, smoke split, 1 seed: ROUGE-1 0.378, ROUGE-2 0.098, ROUGE-L 0.206, ROUGE-SU4 0.129 (TextRank: 0.260 / 0.040 / 0.150 / 0.076). About 8 s per meeting.

## 2.13 Synthetic data, recursive mode and scores by group

**Synthetic data** (scored by default after AMI; `--dataset synthetic` for it alone). The 100 LLM-generated meetings in `5_data/synthetic/data/raw` (25 series of 4), each built around two planned edge cases from `scenarios.yaml` (a plan that changes, a self-correction, an off-topic tangent, an absent person, sarcasm, interleaved topics, tasks given to absent people, reassigned or dropped tasks, ...). Its own committed split (`data/splits/synthetic_splits.json`, whole series per split): `eval` is its 20 test meetings, `smoke` the first dev meeting of each feature (F1, F2, F3). The reference is the meeting's `summary.abstract`, the same section AMI's reference uses, and speakers become roles (PM, ME, ...) as in AMI. Before a run, every file is checked against the sha256 in its `manifest.json`; the run's `data_hash` is the manifest's own sha256.

**Recursive mode** (default; `--mode full` for the old behaviour). The meeting is fed in chunks, and after each one the model turns (summary so far + new chunk) into a new summary so far, which is what the live bot does:

| Model | Recursive | Full |
|---|---|---|
| TextRank | every 30 s: rank (lines kept so far + new lines), keep 1.5x the word budget (the buffer, so a line that only becomes central later isn't dropped too early); at the end, trim to the budget | one ranking over the whole meeting |
| BART | every 60 s window: summarize ("Summary: <so far>" + the window as chat lines); the minimum length grows with the summary so it doesn't collapse | each window, then a summary of the summaries |
| LLM | every 30 s: notes so far + new lines (always worked this way) | one call with the whole transcript |

Recursive BART makes one longer generation per window, so it is slower on CPU than full mode.

**Scores by group** (`metrics_by_group` in every log line, and printed at the end of a run). Summaries are per meeting, so the groups describe meetings:

| Dataset | Group | Values |
|---|---|---|
| AMI | `gender_mix` (from `participants.xml` sex, via `meetings.xml` global_name) | `all_male`, `mixed`, `all_female` |
| AMI | `non_native` (native language other than English) | `all_native`, `some_non_native` (1-2 speakers), `mostly_non_native` (3+) |
| synthetic | `feature` (what the meeting was generated to test) | `F1` live summary, `F2` action items, `F3` Q&A |
| synthetic | `scenario` (edge cases in the meeting; each meeting has two, so it counts in both) | the 13 scenario IDs |

Each group shows `n` (meetings), `n_series` and the mean of every metric; each breakdown shows the `gap` (best minus worst group) and the `worst` group per metric. **Read small groups with care:** AMI meetings come in series of 4 with the same 4 people, so `n_series` is the real sample size, and groups with one series are flagged "indicative only". On the AMI eval split the `all_female` and `mostly_non_native` groups are the same single series, so those two can't be told apart. Each meeting's groups (and the raw counts: speakers, female speakers, non-native speakers) are in `meeting_groups`, so other groupings can be computed from the log later.

First TextRank numbers (eval split, 1 seed, not logged): AMI full 0.238 / recursive 0.232 ROUGE-1; synthetic full 0.368 / recursive 0.330.
