# Hyperparameter search (TextRank and BART)

Finds better settings for the two non-LLM summarizers. Everything the search produces is logged in this folder
(`results/`), and nothing here writes to `6_evaluation/summarization/results/`.

**No test leakage.** The searches only score the **dev** splits of both datasets (AMI and synthetic). The test
splits are scored once at the end, with the default settings and with the best settings side by side, by
`run.py --results-dir 2_summarization/hpo/results/eval`.

**Objective.** For each dataset, the mean over its dev meetings of (ROUGE-1 + ROUGE-2 + ROUGE-L) / 3. Then the
mean of the two datasets, so each counts equally.

## Files

| File | What it does |
|---|---|
| `common.py` | Dev meetings of both datasets, scoring, and the trial log (resumable: done trials are skipped) |
| `tune_textrank.py` | Full grid in full mode, plus random recursive-mode trials. Parallel, a few minutes |
| `tune_bart.py` | Staged search on 5 + 5 dev meetings, with every BART generation cached in `cache/` (gitignored). Takes hours on a CPU |
| `overnight.sh` | TextRank test evals (default vs tuned, both modes), then the BART search, then BART test evals |
| `llm_120s.json`, `llm_recursive.sh` | Gemini recursive eval with a 120 s update interval, for comparison (not tuned). The script retries until done; every call is saved, so a stopped run resumes |

## What is logged (`results/`)

| File | Contents |
|---|---|
| `textrank_trials.jsonl`, `bart_trials.jsonl` | One line per trial: params, objective, and per dataset the ROUGE-1/2/L/SU4 means, summary length, **every meeting's scores**, **scores per group** (AMI gender / non-native mix; synthetic feature tier / scenario) and the gap between groups. BART trials also keep every summary |
| `textrank_best.json`, `bart_best.json` | Best settings per mode, read by `run.py --params` |
| `textrank_report.json` | Default vs best trial per mode |
| `textrank_search.log`, `overnight.log`, `llm_eval.log` | Console output of the runs |
| `eval/llm_cache.jsonl` | Every Gemini call of the evals (prompt and reply), for resuming. Gitignored |
| `eval/results_log.jsonl`, `eval/runs/` | Test-split runs of `run.py` (default and tuned): the same record as the main results log, with seeds, hashes and groups |

## Commands (from the repo root)

```
python 2_summarization/hpo/tune_textrank.py
bash 2_summarization/hpo/overnight.sh          # Git Bash on Windows
python 2_summarization/run.py --model textrank --split eval --params 2_summarization/hpo/results/textrank_best.json
```

## Search spaces

**TextRank:**
- `words_scale`: summary budget × the train split's average summary length.
- `min_line_words`: shortest line kept.
- `clean`: drop fillers and stutters.
- `speaker_names`: prefix lines with the role.
- Recursive only: `chunk_seconds` and `buffer`.

**BART** (defaults are today's behaviour; see `bart_summarizer.py`):
- Full mode:
  1. `window_seconds` × `window_max_tokens`;
  2. `final_max_tokens` × `final_min_tokens`;
  3. `length_penalty`.
- Recursive mode:
  1. `window_seconds`;
  2. `rec_max_tokens`, `floor_ratio`, `floor_cap` and `length_penalty`.

## Results

Run on 2026-11-19/20, from commit 7cea72e plus uncommitted changes. The best settings are in
`results/textrank_best.json` and `results/bart_best.json`.

### Best settings

| Model, mode | Best settings | Dev objective (default → best) |
|---|---|---|
| TextRank, full | `words_scale` 1.5, `min_line_words` 16, `clean`, `speaker_names` | 0.167 → 0.207 (192-trial grid) |
| TextRank, recursive | same, plus `chunk_seconds` 120, `buffer` 1.0 | 0.152 → 0.199 (310 trials) |
| BART, full | `window_seconds` 60, `window_max_tokens` 100, `final_max_tokens` 150, `final_min_tokens` 120, `length_penalty` 2.0 | 0.213 → 0.266 (23 trials) |
| BART, recursive | `window_seconds` 180, `rec_max_tokens` 300, `floor_ratio` 1.0, `floor_cap` 150 | 0.182 → 0.224 (8 trials) |

What made the difference:
- **Longer summaries.** Both models' default summaries were shorter than the human ones.
- **TextRank:** dropping short lines (under 16 words), which are mostly backchannel and filler.
- **BART:** a much higher minimum length for the final summary (120 tokens, up from 60).

**Caveat on `speaker_names`.** Prefixing lines with the role ("Project Manager: …") helps partly because the
human summaries name the roles too, so the role words add ROUGE overlap. Without it, the best dev objective is
0.192 in full mode and 0.191 in recursive mode, still well above the defaults.

### Test split (scored once, after the search)

TextRank was scored over 20 seeds; it is deterministic, so std = 0. BART was scored with 1 seed, as beam search
is deterministic, and with BERTScore. Each cell is ROUGE-1 / ROUGE-2 / ROUGE-L.

| Model, mode, dataset | Default | Tuned | BERTScore F1 (default → tuned) |
|---|---|---|---|
| TextRank, recursive, AMI | 0.232 / 0.030 / 0.126 | **0.312 / 0.055 / 0.162** | – |
| TextRank, recursive, synthetic | 0.330 / 0.079 / 0.177 | **0.417 / 0.115 / 0.218** | – |
| TextRank, full, AMI | 0.238 / 0.027 / 0.129 | **0.321 / 0.058 / 0.162** | – |
| TextRank, full, synthetic | 0.368 / 0.092 / 0.194 | **0.419 / 0.116 / 0.220** | – |
| BART, recursive, AMI | 0.244 / 0.063 / 0.145 | **0.350 / 0.088 / 0.191** | 0.850 → 0.852 |
| BART, recursive, synthetic | 0.311 / 0.113 / 0.189 | **0.355 / 0.134 / 0.216** | 0.855 → 0.859 |
| BART, full, AMI | 0.304 / 0.084 / 0.178 | **0.386 / 0.095 / 0.206** | 0.854 → 0.853 |
| BART, full, synthetic | 0.320 / 0.123 / 0.210 | **0.387 / 0.139 / 0.233** | 0.859 → 0.859 |

Every ROUGE score improves on the test split. BERTScore stays flat, so the gain comes mostly from length and
coverage rather than from different content. Tuned BART in full mode is now the best non-LLM model on ROUGE-1
for AMI (0.386). On synthetic, TextRank scores higher on ROUGE-1 and ROUGE-L, and BART scores higher on ROUGE-2.

### Groups (test, ROUGE-1)

Small groups (fewer than 2 meeting series) are indicative only.

| Model, mode | AMI: gender-mix gap (default → tuned) | AMI: non-native gap (default → tuned) | Synthetic: feature-tier gap (default → tuned) |
|---|---|---|---|
| TextRank, recursive | 0.087 → 0.031 | 0.054 → 0.050 | 0.032 → 0.055 |
| TextRank, full | 0.024 → 0.037 | 0.009 → 0.051 | 0.022 → 0.049 |
| BART, recursive | 0.031 → 0.051 | 0.034 → 0.035 | 0.020 → 0.053 |
| BART, full | 0.013 → 0.014 | 0.018 → 0.010 | 0.034 → 0.030 |

- **Tuning raises every group's score.** In some runs the gaps widen, because some groups gain more than others.
- **Tuned BART, full mode, is the most even:** all gaps are 0.03 or less.
- **The non-native comparison is weak.** Only `all_native` has 2 or more series in the test split, so the other
  non-native groups are small and the comparison is only indicative.
- **Synthetic tiers:** F3 (one series) scores highest in the tuned runs.
- **Per-meeting detail** for every run (scores, summaries, groups) is in `eval/runs/*.json` under
  `per_seed_runs[0]`.

### Comparison with the LLM (test, not tuned)

Gemini 3.5 Flash-Lite, scored the same way (1 seed, BERTScore). Full mode is one call per meeting (40 calls in
total). Recursive mode updates the notes every **120 s** (428 calls), not the live bot's 30 s, to fit the free tier's
500 requests a day. This also matches tuned TextRank's 120 s; tuned BART uses 180 s. Each cell is
ROUGE-1 / ROUGE-2 / ROUGE-L; BERTScore is rescaled with the RoBERTa-large baseline 0.831.

| Model, mode | AMI | AMI BERTScore | Synthetic | Synthetic BERTScore |
|---|---|---|---|---|
| TextRank tuned, full | 0.321 / 0.058 / 0.162 | – | 0.419 / 0.116 / 0.220 | – |
| BART tuned, full | 0.386 / 0.095 / 0.206 | 0.129 | 0.387 / 0.139 / 0.233 | 0.166 |
| **Gemini, full** | **0.389** / 0.093 / **0.217** | **0.222** | **0.445 / 0.181 / 0.287** | **0.260** |
| TextRank tuned, recursive | 0.312 / 0.055 / 0.162 | – | 0.417 / 0.115 / 0.218 | – |
| BART tuned, recursive | 0.350 / 0.088 / 0.191 | 0.123 | 0.355 / 0.134 / 0.216 | 0.162 |
| Gemini, recursive (120 s) | 0.305 / 0.071 / 0.179 | 0.166 | 0.381 / 0.140 / 0.243 | 0.207 |

- **Full mode:** Gemini is best on every metric except AMI ROUGE-2, where it ties tuned BART. On BERTScore it is
  the only model above ~0.20, the score another meeting's human summary already gets (one AMI meeting checked).
- **Recursive mode:** Gemini loses ROUGE when it rewrites its notes chunk by chunk. Its summaries are shorter
  (AMI 77 words vs 100 in full mode; target 160) and its ROUGE-1 falls below tuned BART's. Its BERTScore stays
  above BART's. Gemini was not tuned, unlike BART and TextRank. Its prompt and summary length are the obvious
  next things to try.
- **Groups (Gemini, recursive):**
  - AMI gender-mix gap 0.042 (all-male lowest, 0.281); non-native gap 0.028.
  - On synthetic, F3 is the weakest tier (0.309; one series, indicative only): F3_TASK_TO_ABSENT 0.288 and
    F3_ACTION_REMOVED 0.314. Notes that are rewritten often seem to drop removed or reassigned items.
