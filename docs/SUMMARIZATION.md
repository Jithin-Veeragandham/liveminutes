# 2. Summarization (Feature 1): full reference

Feature 1: summarize a meeting. The short version (quick start, results, where things are recorded) is in the [README](../README.md). This file has the details: seeds, the step-by-step flow, every file, the split and leakage.

- **Data:** AMI meeting corpus (real meetings + human-written summaries, free, CC BY 4.0)
- **Baseline:** TextRank. Picks the most important lines from the transcript. No training, CPU, under 1 s per meeting.
- **Deep model:** BART (`philschmid/bart-large-cnn-samsum`). Writes new sentences. Not trained on AMI. About 45 s per meeting on CPU.
- **Score:** ROUGE (word overlap with the human summary). BERTScore (meaning overlap) for BART.

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
| Only check the data | `.venv/bin/python 5_data/ami/fetch_data.py --verify` |
| Only check the split | `.venv/bin/python 5_data/ami/split.py --check` |
| Run without saving a result | add `--no-log` |

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
1. check data     -> fetch_data.py: download AMI if missing, check its hash against MANIFEST.json
2. load split     -> split.py: load train/dev/eval, check it against MANIFEST.json, check no person is in two splits
3. build model    -> TextRank (or BART)
4. run            -> for each seed: set seeds, summarize each meeting (ami.py reads it), score it (metrics.py)
5. average        -> mean and std over seeds, and seed_variation (what changed between seeds, and why)
6. save           -> one line in 6_evaluation/summarization/results/results_log.jsonl
```

## 2.4 What each file does

| File | What it does |
|---|---|
| `2_summarization/run.py` | **Start here.** The one command. Seeds at the top. Runs the 6 steps above. |
| `2_summarization/settings.py` | Model settings: TextRank, BART and BERTScore versions. |
| `2_summarization/textrank_baseline.py` | The baseline. ~30 lines. |
| `2_summarization/bart_summarizer.py` | The deep model. Summarizes ~60 s chunks, then combines them. |
| `5_data/ami/ami_settings.py` | Where the AMI data lives, and the 3 smoke-test meetings. |
| `5_data/ami/fetch_data.py` | Downloads AMI and checks it matches the expected hash. |
| `5_data/ami/split.py` | Decides which meetings go to train / dev / eval. Checks no person is in two splits. |
| `5_data/ami/ami.py` | Reads AMI's XML files into transcript lines (speaker, time, text) and the human summary. |
| `5_data/ami/MANIFEST.json` | Where the data came from, when, and its hashes; also the split's paths and hashes (`split`). |
| `5_data/ami/LICENSE_AMI.txt` | AMI licence (CC BY 4.0). |
| `5_data/ami/ami_scenario_split.json` | The saved split. |
| `5_data/raw/ami/` | Downloaded data. **Not committed** (gitignored). |
| `6_evaluation/summarization/metrics.py` | ROUGE-1, ROUGE-2, ROUGE-L and BERTScore. |
| `6_evaluation/summarization/results/results_log.jsonl` | One line per run: scores + everything needed to repeat it. |
| `6_evaluation/summarization/results/runs/<run_id>.json` | Same, plus every summary the model wrote, per seed and meeting. |

**Environment files are at the repo root:** one `requirements.txt` (section 1 ingestion, section 2 summarization baseline, section 3 BART + BERTScore; every summarization package pinned) and `.python-version` (3.12). The ingestion Docker image installs only section 1, so torch (~2 GB) stays out of it.

## 2.5 Libraries we used

| Part | Library | Version |
|---|---|---|
| Baseline model (TextRank) | `summa` | 1.2.0 |
| Deep model (BART) | `transformers` + `torch` | 5.20.2 / 2.14.1 |
| ROUGE-1, ROUGE-2, ROUGE-L | `rouge-score` (Google's package, stemming on) | 0.1.2 |
| BERTScore | `bert-score` (model: `roberta-large`, pinned) | 0.3.13 |

All scores are F-measures from 0 to 1: our summary compared with AMI's human summary.

## 2.6 Results

The numbers are in `6_evaluation/summarization/results/results_log.jsonl`. **TextRank baseline, 20 seeds:**

| Split | ROUGE-1 | ROUGE-2 | ROUGE-L | Std over seeds | Time per meeting |
|---|---|---|---|---|---|
| smoke (3 meetings) | 0.260 | 0.040 | 0.150 | 0 | 0.05 s |
| **eval (20 test meetings)** | **0.238** | **0.027** | **0.129** | 0 | 0.2 s |

- **Compared with the paper:** published TextRank ROUGE-1 on AMI is 35.25 (Zhu et al. 2020); ours is 23.8. Likely reasons, not checked yet: different summary length and a different set of test meetings.
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
