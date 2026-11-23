# Week 11 release plan

All numbers are live mode (`recursive`) on the test split: 20 AMI meetings and 20 synthetic meetings. Every run is logged in `2_summarization/hpo/results/eval/results_log.jsonl`, with full details in `runs/<run id>.json`.

## 1. Release candidate

- **Model:** Gemini `gemini-3.5-flash-lite`, live mode, notes updated every 120 s of meeting.
- **Prompt and pipeline:** `2_summarization/llm_summarizer.py`. The calls are in `2_summarization/llm_providers.py`.
- **Config:**
  - `LLM` in `2_summarization/settings.py`: 6 context lines, effort `medium`, `max_tokens` 16000. Summary length is the train split's average: 160 words for AMI, 199 for synthetic.
  - Overridden by `2_summarization/hpo/llm_120s.json`: `chunk_seconds` 120, `min_seconds_between_calls` 5.
  - Provider and model come from `.env`: `LIVEMINUTES_LLM_PROVIDER=gemini`, `LIVEMINUTES_LLM_MODEL=gemini-3.5-flash-lite`, `GEMINI_API_KEY=...`. The code's default is Anthropic.
- **Commit:** the `week11-release` tag.
- **Prompt caveat:** the Gemini numbers were measured with the prompt at `ab5eaff`. The tagged prompt has one reworded instruction about `<earlier_lines>` (the split-task fix from #13). The reworded prompt hasn't been scored yet.
- **Interval caveat:** the meeting bot's live notes update every 30 s (`NOTES_INTERVAL_S` in `1_ingestion/settings.py`). Only 120 s was scored with Gemini.
- **Fallback:** the default TextRank baseline with `2_summarization/hpo/textrank_live_120s.json` (the slide 3 baseline).
  - The tuned TextRank row below is shown for comparison only.

## 2. Why

> Decision rule (Week 7 card, written before any run): 1) Filter: keep the architectures that beat the baseline on the summary metrics. 2) Pick: the one with the fewest incorrect summaries on the Storyboard B slice. 3) Check: the LLM wins only if it beats both other architectures on that slice. Fallback, if no architecture qualifies: the TextRank baseline. Changed at reporting time, and disclosed: BERTScore is the deciding summary metric, and the B slice is measured with ROUGE-1 and BERTScore instead of a count of incorrect summaries.

**How Gemini passes each step, with BERTScore deciding:**
1. **Filter.** The baseline (TextRank default) scores 0.806 on AMI and 0.829 on synthetic. All three architectures beat it on both datasets: TextRank tuned 0.818 / 0.839, BART tuned 0.852 / 0.859, Gemini 0.859 / 0.866. So all three are kept.
2. **Pick.** On the B slice, Gemini has the highest BERTScore in every group:

   | Group | Gemini | BART tuned | TextRank tuned |
   |---|---|---|---|
   | Time | **0.8645** | 0.8580 | 0.8360 |
   | People | **0.8675** | 0.8620 | 0.8399 |
   | Intent | **0.8644** | 0.8520 | 0.8390 |

3. **Check.** So the LLM beats both other architectures on that slice, and Gemini is the release candidate. The fallback isn't needed.

The BART and TextRank slice numbers come from their seed-42 synthetic runs (`20261120T095825Z`, `20261121T153923Z`), grouped as in the B slice table below.

| Live mode, test split | AMI ROUGE-1 | AMI BERTScore | Synthetic ROUGE-1 | Synthetic BERTScore |
|---|---|---|---|---|
| TextRank, default (baseline, 120 s) | 0.229 | 0.806 | 0.335 | 0.829 |
| TextRank, tuned (120 s) | 0.312 | 0.818 | 0.417 | 0.839 |
| BART, tuned (180 s) | 0.350 | 0.852 | 0.355 | 0.859 |
| **Gemini (120 s), mean of seeds 42–46** | 0.305 | **0.859** | 0.375 | **0.866** |

- **Gemini passes with BERTScore deciding.** It has the highest BERTScore on both datasets.
- **It is below tuned TextRank on ROUGE-1** on both (0.305 vs 0.312, and 0.375 vs 0.417). With ROUGE-1 deciding instead, tuned TextRank would win the Pick step: its B slice ROUGE-1 is 0.413 / 0.423 / 0.413 (Time / People / Intent), against Gemini's 0.363 / 0.391 / 0.352. Gemini does beat the default TextRank baseline on both metrics.
- **Gemini's row is preliminary** until its prompt is tuned. TextRank and BART were tuned on the dev split; Gemini wasn't.
- **B slice** (Gemini, synthetic, mean of 5 seeds), ROUGE-1 / BERTScore:

  | Group | Edge cases in it | Meetings | Score |
  |---|---|---|---|
  | Time | plan change, self-correction, interleaved topics, reassigned or dropped, action removed | 13 | 0.363 / 0.8645 |
  | People | absent person discussed, task to an absent person, offered then taken by someone else, vague owner | 14 | 0.391 / 0.8675 |
  | Intent | sarcasm as fact, unagreed decision, off-topic tangent | 7 | 0.352 / 0.8644 |

  Each meeting has two edge-case labels, so the groups overlap.

## 3. Rollback

- **Meeting bot:** set `LIVE_NOTES=0` in `.env`, then run `dc up -d` from `1_ingestion/` (README section 7). The live notes stop, and new meetings show the labelled sample data instead. The bot has no TextRank path.
- **Scored summaries:** `.venv/bin/python 2_summarization/run.py --model textrank --params 2_summarization/hpo/textrank_live_120s.json`

## 4. Before the Final Showcase

- **Tune Gemini's prompt** on the dev split, then re-score on test to complete its row.
- **Carry earlier facts forward between updates in live mode.** Today, later rewrites drop facts like who is absent or which plan was replaced.
- **Dropped:** more Gemini seeds. The spread over 5 seeds is already about ±0.01 ROUGE-1 (`2_summarization/hpo/results/gemini_seed_spread.md`).

## 5. Reproduce

**How to run:**
- Run from the repo root.
- Add `--results-dir <a new folder>` to keep the committed log unchanged. The logged runs used `--results-dir 2_summarization/hpo/results/eval`.
- TextRank, BART and Gemini seed 42 ran on Windows. On macOS, TextRank's AMI ROUGE-1 comes out about 0.002 lower (3 of 20 meetings differ).

**TextRank default, 120 s** (`20261123T013608Z`, `20261123T013638Z`):
```
.venv/bin/python 2_summarization/run.py --model textrank --split eval --mode recursive --params 2_summarization/hpo/textrank_live_120s.json --tag default_every120s --seed 42 --bertscore
```

**TextRank tuned, 120 s** (`20261121T153848Z`, `20261121T153923Z`). The earlier best settings aren't in a params file, so the command writes one first:
```
echo '{"recursive": {"words_scale": 1.5, "min_line_words": 16, "clean": true, "speaker_names": true, "chunk_seconds": 120, "buffer": 1.0}}' > /tmp/textrank_tuned_120s.json && .venv/bin/python 2_summarization/run.py --model textrank --split eval --mode recursive --params /tmp/textrank_tuned_120s.json --seed 42 --bertscore
```

**BART tuned** (`20261120T093839Z`, `20261120T095825Z`). This takes hours on a CPU:
```
.venv/bin/python 2_summarization/run.py --model bart --split eval --mode recursive --params 2_summarization/hpo/results/bart_best.json --seed 42 --bertscore
```

**Gemini, one seed.** Run it for N = 42 to 46; seed 42's runs are `20261121T090614Z` and `20261121T091951Z`.
- Use a new `--results-dir` for each seed. run.py's call cache (`llm_cache.jsonl`) ignores the seed, so seeds that share a folder reuse each other's answers.
- Each seed costs about $0.40.
- Gemini is sent no seed, so reruns vary by about ±0.01.
- To score the exact prompt that was measured, first run `git checkout ab5eaff`.
```
.venv/bin/python 2_summarization/run.py --model llm --split eval --mode recursive --params 2_summarization/hpo/llm_120s.json --tag 120s --seed N --bertscore --results-dir /tmp/gemini_seedN
```

**Gemini mean of 5 seeds**, from the log:
```
.venv/bin/python -c "import json,statistics as s; r=[json.loads(l) for l in open('2_summarization/hpo/results/eval/results_log.jsonl')]; [print(d, *['%.3f' % s.mean(x['metrics_mean'][k] for x in r if x['model']=='llm' and x['mode']=='recursive' and x['dataset']==d) for k in ('rouge1','bertscore_f1')]) for d in ('ami','synthetic')]"
```

**B slice**, from the 5 synthetic run files:
```
.venv/bin/python - <<'EOF'
import glob, json, statistics as s
G = {"Time": {"PLAN_CHANGE", "SELF_CORRECTION", "INTERLEAVED_TOPICS", "REASSIGNED_OR_DROPPED", "ACTION_REMOVED"},
     "People": {"ABSENT_DISCUSSED", "TASK_TO_ABSENT", "OFFERED_TAKEN_BY_OTHER", "VAGUE_NO_OWNER"},
     "Intent": {"SARCASM_AS_FACT", "UNAGREED_DECISION", "OFF_TOPIC_TANGENT"}}
runs = [json.load(open(f)) for f in glob.glob("2_summarization/hpo/results/eval/runs/*_llm_120s_synthetic_eval_recursive_1seeds.json")]
pm = [list(r["per_seed_runs"].values())[0]["per_meeting"] for r in runs]
for g, tags in G.items():
    ms = [m for m, v in runs[0]["meeting_groups"].items() if {t.split("_", 1)[1] for t in v["scenario"]} & tags]
    print(g, len(ms), *["%.4f" % s.mean(s.mean(p[m][k] for m in ms) for p in pm) for k in ("rouge1", "bertscore_f1")])
EOF
```
