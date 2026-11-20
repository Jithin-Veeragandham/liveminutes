# Action items (Feature 2): two baselines and their evaluation

From a meeting transcript, find each action item: the **task**, its **owner** and its **due date**, plus the line(s) it came from. Output is the UI contract's action-item format ([UI_CONTRACT.md](UI_CONTRACT.md) on the `ui-shared-view-and-editing` branch): `text`, `owner`, `due`, `source_line_ids` (plus `id`, `status`, `version`). So the baselines can plug into the meeting page, and any system, including the LLM, is scored by the same code.

Code: [`3_action_items/`](../3_action_items/) (baselines) and [`6_evaluation/action_items/`](../6_evaluation/action_items/) (data, matching, metrics, breakdowns, results). Settings: [`3_action_items/settings.py`](../3_action_items/settings.py). Errors read by hand: [`6_evaluation/action_items/ERRORS.md`](../6_evaluation/action_items/ERRORS.md).

## Results

**Final numbers** (AMI eval = 20 meetings, synthetic test = 20 meetings; nothing was tuned on them). 95% bootstrap intervals over meetings. Logged at commit `e293eea` with no uncommitted changes, on an Apple M5 (MPS), Python 3.12.14, torch 2.14.1, transformers 5.20.2.

| System | Data | Meetings | Gold | Predicted | Matched | Precision | Recall | F1 | Owner acc. (n) | Due acc. (n) |
|---|---|---|---|---|---|---|---|---|---|---|
| rules | ami eval | 20 | 30 | 91 | 5 | 0.05 [0.01, 0.09] | 0.17 [0.04, 0.33] | **0.08 [0.02, 0.14]** | 0.40 [0.00, 1.00] (5) | n/a (0) |
| zeroshot | ami eval | 20 | 30 | 81 | 5 | 0.06 [0.02, 0.10] | 0.17 [0.05, 0.31] | **0.09 [0.03, 0.15]** | 0.40 [0.00, 1.00] (5) | n/a (0) |
| rules | synthetic test | 20 | 112 | 146 | 85 | 0.58 [0.53, 0.64] | 0.76 [0.71, 0.81] | **0.66 [0.61, 0.71]** | 0.88 [0.82, 0.94] (85) | 0.80 [0.50, 1.00] (10) |
| zeroshot | synthetic test | 20 | 112 | 90 | 40 | 0.44 [0.35, 0.53] | 0.36 [0.27, 0.44] | **0.40 [0.31, 0.47]** | 0.95 [0.88, 1.00] (40) | 0.83 [0.00, 1.00] (6) |

- **Synthetic: the rules are clearly better** (F1 0.66 vs 0.40, intervals apart). The zero-shot classifier finds only about a third of the tasks at the threshold dev chose. When it does, its owners are right more often (0.95 vs 0.88), because the lines it keeps are the explicit ones.
- **AMI: both are near zero and can't be told apart** (F1 0.08 and 0.09, intervals overlapping). AMI's actions are summary sentences about whole discussions ("The Industrial Designer will prepare the working design"), said indirectly in the meeting, and 3 of the 20 eval meetings have none. Only 5 of 30 gold items are matched by either system, so owner accuracy (n = 5) can't be read.
- AMI dev was higher than AMI eval for both (F1 0.21 and 0.23 on dev), so the eval drop isn't from overfitting one baseline. The gold differs a lot between the two sets of meetings.
- **Due dates**: neither dataset labels them. On the few matched items whose gold text names a time (10 and 6 on synthetic), most due dates agree; too few to read.
- **Dev numbers** for comparison: rules AMI 0.21 / synthetic 0.66, zero-shot AMI 0.23 / synthetic 0.42 (`report.py --split dev`).

### Breakdowns

Groups were fixed before any eval or test run. **Every group here has 8 or fewer meetings (or 4 or fewer edge-case instances), so none can be read with confidence**; groups with n ≤ 4 are marked "too small". On AMI, a team's 4 meetings share one speaker group, so "all native" is one team.

**ami breakdowns** (F1 and owner accuracy, 95% bootstrap intervals over meetings or instances)

| speakers | n | Gold | rules F1 | rules owner | zeroshot F1 | zeroshot owner |
|---|---|---|---|---|---|---|
| all native (too small) | 4 | 6 | 0.00 [0.00, 0.00] | n/a | 0.16 [0.00, 0.26] | 0.50 [0.00, 1.00] |
| all non-native | 8 | 13 | 0.14 [0.04, 0.25] | 0.50 [0.00, 1.00] | 0.10 [0.03, 0.19] | 0.33 [0.00, 1.00] |
| mixed | 8 | 11 | 0.05 [0.00, 0.13] | 0.00 [0.00, 0.00] | 0.00 [0.00, 0.00] | n/a |

| gender | n | Gold | rules F1 | rules owner | zeroshot F1 | zeroshot owner |
|---|---|---|---|---|---|---|
| all female (too small) | 4 | 7 | 0.00 [0.00, 0.00] | n/a | 0.00 [0.00, 0.00] | n/a |
| all male | 8 | 13 | 0.14 [0.04, 0.25] | 0.50 [0.00, 1.00] | 0.10 [0.03, 0.19] | 0.33 [0.00, 1.00] |
| mixed | 8 | 10 | 0.04 [0.00, 0.12] | 0.00 [0.00, 0.00] | 0.10 [0.00, 0.19] | 0.50 [0.00, 1.00] |

| type | n | Gold | rules F1 | rules owner | zeroshot F1 | zeroshot owner |
|---|---|---|---|---|---|---|
| a (kickoff) | 5 | 10 | 0.16 [0.00, 0.33] | 0.50 [0.00, 1.00] | 0.06 [0.00, 0.14] | 0.00 [0.00, 0.00] |
| b+c (middle) | 10 | 15 | 0.00 [0.00, 0.00] | n/a | 0.09 [0.00, 0.18] | 0.00 [0.00, 0.00] |
| d (final) | 5 | 5 | 0.19 [0.08, 0.26] | 0.33 [0.00, 1.00] | 0.12 [0.00, 0.24] | 1.00 [1.00, 1.00] |


**synthetic breakdowns** (F1 and owner accuracy, 95% bootstrap intervals over meetings or instances)

| feature | n | Gold | rules F1 | rules owner | zeroshot F1 | zeroshot owner |
|---|---|---|---|---|---|---|
| F1 | 8 | 43 | 0.68 [0.64, 0.72] | 0.84 [0.73, 0.94] | 0.40 [0.27, 0.51] | 0.94 [0.79, 1.00] |
| F2 | 8 | 46 | 0.71 [0.63, 0.79] | 0.87 [0.78, 0.95] | 0.36 [0.28, 0.45] | 1.00 [1.00, 1.00] |
| F3 (too small) | 4 | 23 | 0.53 [0.50, 0.57] | 1.00 [1.00, 1.00] | 0.45 [0.15, 0.58] | 0.91 [0.67, 1.00] |

| edge case | n | Gold | rules F1 | rules owner | zeroshot F1 | zeroshot owner |
|---|---|---|---|---|---|---|
| F1_ABSENT_DISCUSSED (too small) | 3 | 7 | 0.43 [0.00, 0.57] | 0.67 [0.00, 1.00] | 0.60 [0.00, 1.00] | 0.67 [0.50, 1.00] |
| F1_INTERLEAVED_TOPICS (too small) | 3 | 2 | 0.00 [0.00, 0.00] | 1.00 [1.00, 1.00] | 0.00 [0.00, 0.00] | 1.00 [1.00, 1.00] |
| F1_OFF_TOPIC_TANGENT (too small) | 2 | 0 | no gold; 1 predicted | n/a | no gold; 0 predicted | n/a |
| F1_PLAN_CHANGE (too small) | 2 | 1 | 0.67 [0.00, 0.67] | 1.00 [1.00, 1.00] | 0.67 [0.00, 0.67] | 1.00 [1.00, 1.00] |
| F1_SARCASM_AS_FACT (too small) | 3 | 0 | no gold; 0 predicted | n/a | no gold; 1 predicted | n/a |
| F1_SELF_CORRECTION (too small) | 3 | 0 | no gold; 0 predicted | n/a | no gold; 0 predicted | n/a |
| F2_OFFERED_TAKEN_BY_OTHER (too small) | 4 | 4 | 0.73 [0.67, 0.89] | 0.50 [0.00, 1.00] | 0.00 [0.00, 0.00] | n/a |
| F2_REASSIGNED_OR_DROPPED (too small) | 4 | 5 | 0.46 [0.15, 0.80] | 0.33 [0.00, 1.00] | 0.25 [0.00, 0.55] | 1.00 [1.00, 1.00] |
| F2_TASK_TO_ABSENT (too small) | 4 | 9 | 0.58 [0.51, 0.67] | 0.71 [0.40, 1.00] | 0.31 [0.00, 0.50] | 1.00 [1.00, 1.00] |
| F2_VAGUE_NO_OWNER (too small) | 4 | 4 | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 0.00 [0.00, 0.00] | n/a |
| F3_ACTION_REMOVED (too small) | 2 | 0 | no gold; 2 predicted | n/a | no gold; 0 predicted | n/a |
| F3_TASK_TO_ABSENT (too small) | 3 | 6 | 0.57 [0.40, 1.00] | 1.00 [1.00, 1.00] | 0.55 [0.00, 1.00] | 1.00 [1.00, 1.00] |
| F3_UNAGREED_DECISION (too small) | 3 | 0 | no gold; 0 predicted | n/a | no gold; 1 predicted | n/a |

For edge cases with no gold items in their lines (an off-topic tangent, sarcasm, a self-correction, a removed action, an unagreed decision), what counts is how many items were predicted there; each is a false alarm. The rules predicted the **2 removed actions** (`F3_ACTION_REMOVED`) as tasks: their drop rule needs the task's words repeated.

### Time (Apple M5, MPS)

| Run | First time | From cache |
|---|---|---|
| Smoke test, both baselines (2 AMI dev + 1 synthetic dev meetings) | 20 s (plus the one-time model download, about 1.7 GB) | 9 s |
| Rules, 20 meetings | about 1 s | the same |
| Zero-shot, AMI eval (6,742 lines scored, 33 lines/s incl. loading) | 205 s | under 1 s |
| Zero-shot, synthetic test (1,818 lines) | 49 s | under 1 s |
| All final runs (`--split final`) | 4.5 min | 12 s |
| Zero-shot dev tuning (16 settings, about 159,000 line scores) | 85 min | 51 s |

On CPU the zero-shot scoring is about twice as slow (26 vs 54 lines per second measured on this machine).


## 1. Data and ground truth

| | AMI | Synthetic |
|---|---|---|
| Source | `5_data/ami` (read only): transcripts as `ami.py` builds them, one line per dialogue act | `5_data/synthetic` on the `synthetic-data` branch, read from git at commit `d0cbb4b` (read only, never checked out or changed) |
| Gold | the **actions** section of each human summary (`abstractive/<meeting>.abssumm.xml`); `*NA*` = none | `action_items` with status `active` or `reassigned`. A **dropped** task is not an action item at the end of the meeting, so it is left out (predicting it is an error). |
| Owner | AMI actions name roles: "The Industrial Designer will ...". The roles in the subject become the owners, mapped to the meeting's speakers (AMI speakers are their roles). Also: "X instructed Y to ..." → Y; "... and the Industrial Designer will ..." adds ID; "He will also ..." → the owner of the sentence before. No role ("The team", "All participants"): unassigned. | the final owner's label; `null` / `owner_unclear` → unassigned |
| Due | not labelled. Taken from the gold sentence with the same date parser (`due.py`): "at the next meeting" → `next_meeting`. | not labelled; taken from the description the same way ("for the next meeting", "tonight", "Thursday") |
| Lines each item came from | the extractive summary links (`extractive/<meeting>.summlink.xml`, read from the downloaded zip, whose SHA-256 is checked against MANIFEST.json) | `evidence_segments` + `history` segments |
| Splits | dev 20 meetings (tuning), **eval 20 meetings (reported)** | its own split: dev 20 (tuning), **test 20 (reported)**; train is not used |
| Gold items | dev 41, eval 30 | dev 117, test 112 |

Line ids follow the UI contract: `"t" + start in ms` (+1 ms if two lines start in the same millisecond). AMI line starts count from the start of the recording; synthetic ones from the start of the meeting.

**Not checked by a person:** the synthetic data has no human-reviewed release yet (`data/reviewed/` is empty), so its gold is the generator's own labels.

## 2. The baselines

### Rules ([`rules.py`](../3_action_items/rules.py), no model)

1. **Clean** the line: lower case, no "um"/"uh", "gonna" → "going to", "I I'll" → "I'll".
2. **Trigger** + **task verb**. A trigger is a commitment or a request phrase; it counts only if a verb of work follows it (after at most 3 small words like "just", "also"):
   - first person ("I'll", "I will", "I can", "I'm going to", "let me", "should I") → owner: the speaker;
   - request ("can you", "could you", "you'll", "you need to", "please", "Priya, ...") → owner: who is asked;
   - third person ("Marcus will", "ask Priya to") → that person;
   - group ("let's", "we need to", "we should", "someone should", "everyone's going to") → unassigned. Only with verbs of work done after the meeting (send, check, research, contact, ...): "we have to make the buttons bigger" is talk about the product.
   - Not "you can / you could / you have to": in meetings that is mostly the generic "you" of a design idea ("you can make them red").
   - Verbs also done in the meeting itself (present, show, discuss, look at) count only with a later time ("next time", "tomorrow"), or when someone is named for them ("Rafael, you'll look at the working design").
   - Explicit non-tasks: "I'll be honest", "can you hear me", "let's move on", "you'll get an email", "do you want to go first", ...
3. **Owner**: the person named in a request; else whoever answers "Sure" / "I can do that" in the next 3 lines; else the next person to speak. "I'll take that" after an offer, a request or "we need to" moves the task to whoever said it. "Put that on Tom", "ask Priya to", "add it to his list" (the person named just before, or the one absent participant) name the owner.
4. **Due**: the first due phrase in the line or the answer (`due.py`): today, tonight, end of day, tomorrow, this week, next week, the next meeting, weekdays, real dates ("November 21", "21st of November", "11/21", ISO), "by 3 pm", "by noon". With the meeting date, it becomes `YYYY-MM-DD`; without one (AMI, synthetic), `due` stays empty and the phrase is in `due_text`.
5. **Merge** near duplicates: the owner answering a request in the next line ("Tom, you'll do the interface?" / "Yeah, I'll write out the commands"), or two items anywhere whose content words overlap by Jaccard ≥ 0.4.
6. **Drop**: "forget that", "no need to ...", "scratch that" about an earlier task removes it.

Item text: from the task verb to the end of its sentence, as said.

### Zero-shot classifier ([`zeroshot.py`](../3_action_items/zeroshot.py))

`facebook/bart-large-mnli` at revision `d7645e12…`, through the transformers zero-shot pipeline. For each line of 3+ words and 2+ content words (with the line before it as context, if chosen), the pipeline gives the probability that the text entails a hypothesis such as "In this text, someone commits to doing a task or is asked to do one." (`multi_label=True`: entailment vs contradiction for that one hypothesis). Lines at or above the threshold are tasks. Owner, due date and merging are the rules' (`rules.item_for_line`, `rules.merge`); a chosen line with no rule trigger keeps the whole line as its text, and its owner comes from "I" (speaker), a name, "you" (who answers) or nobody.

Batched (32), on Apple's MPS if available, else CPU. Scores are cached on disk by model, revision, hypothesis and the exact input (`3_action_items/cache/`, gitignored), so a rerun only scores new lines.

## 3. Evaluation ([`6_evaluation/action_items/`](../6_evaluation/action_items/))

1. **Embed** every predicted text and every gold text with `BAAI/bge-small-en-v1.5` at revision `5c38ec7c…` (CLS vector, L2-normalized). For AMI, the gold text is compared without its owner and modal ("The Marketing Expert will prepare a report" → "Prepare a report"), since predictions are task phrases and the owner is scored on its own.
2. **Match one to one, best first**: take the most similar (predicted, gold) pair, remove both, repeat, while the cosine similarity is at least **0.70**.
3. **Count**, summed over meetings (micro):
   - precision = matched / predicted, recall = matched / gold, F1;
   - **owner accuracy** on matched pairs: the predicted owner (a display name, role, first name or label; "", "team", "everyone" = unassigned) is one of the gold owners, or both are unassigned;
   - **due accuracy** on matched pairs whose gold names a time: the same due key.
4. **95% intervals**: bootstrap over meetings, B = 2000, `numpy.random.default_rng(0)`, percentile method.

**The match threshold** was chosen on dev only, without any baseline output ([`calibrate.py`](../6_evaluation/action_items/calibrate.py), [`results/tuning/match_threshold.json`](../6_evaluation/action_items/results/tuning/match_threshold.json)): for every gold item and every transcript line in its meeting, a pair is "the same task" if the line is one the item was written from. 0.70 separates those pairs best (F1, mean of AMI dev and synthetic dev).

### Breakdowns (fixed before any eval or test result: [`breakdowns.py`](../6_evaluation/action_items/breakdowns.py), commit `137639f`)

- **AMI**, per meeting:
  - speakers: all native English / mixed / all non-native;
  - gender: all male / mixed / all female;
  - meeting type: a (kickoff) / b+c / d (final).
  A team's 4 meetings share the speaker groups. Native language comes from `participants.xml` where it has the person, else from the AMI participant id (`MEO015`: male, Edinburgh, Other language). No Week 9 grouping existed in the repo.
- **Synthetic**:
  - by **edge case type** (13 types), scored on the items in the tagged lines (gold whose evidence is there, predictions whose source lines are there);
  - by the series' target feature (F1 / F2 / F3).
- Every group has its n; **groups with n ≤ 4 are flagged "too small to read"**. With 20 meetings per split, most are.

## 4. Tuning (dev only)

[`3_action_items/tune.py`](../3_action_items/tune.py), objective: the mean of item F1 on AMI dev and synthetic dev (each dataset counts the same). Results in [`results/tuning/`](../6_evaluation/action_items/results/tuning/).

| Baseline | Grid (dev) | Chosen | Dev mean F1 |
|---|---|---|---|
| Rules | `merge_jaccard` 0.3 to 0.7, `lookahead_lines` 2 to 4, item text to the end of the sentence or of the line | 0.4, 3, sentence | 0.435 (AMI 0.21, synthetic 0.66) |
| Zero-shot | 2 scoring modes x 4 hypothesis wordings x context (line alone / with the line before) x 35 thresholds (0.50 to 0.9998) | entailment, "In this text, someone commits to doing a task or is asked to do one.", line alone, 0.9994 | 0.326 (AMI 0.23, synthetic 0.42) |
| Matching | cosine threshold 0.50 to 0.90 | 0.70 | (calibration F1, not item F1) |

Changes made to the zero-shot grid after its first dev run, and why (dev only):
- **Finer thresholds above 0.999.** The first run peaked at 0.999, the top of the grid. The single-hypothesis score is above 0.9 for 81% of AMI dev lines.
- **A second scoring mode** (task hypothesis vs "small talk, a question or a discussion, not a task"). It didn't win.
- **Lines with fewer than 2 content words aren't scored.** Dev errors: fragments like "Okay so this we had" passed the classifier.

All three are in `settings.py` with comments.

The rules themselves (trigger lists, exclusions, owner rules) were written by reading dev transcripts and dev errors only.

## 5. Scoring another system (e.g. the LLM)

1. Get the exact transcripts the baselines read (same lines, same line ids):
   ```bash
   .venv/bin/python 6_evaluation/action_items/export_meetings.py --data ami --split eval --out 5_data/processed/ami_eval.json
   .venv/bin/python 6_evaluation/action_items/export_meetings.py --data synthetic --split test --out 5_data/processed/synthetic_test.json
   ```
   Each meeting has `people` (display names) and `lines` (`id`, `speaker`, `start`, `text`).
2. Run the system on each meeting and write one JSON file:
   ```json
   {"meetings": {"ES2004a": {"items": [
     {"text": "Prepare a report on trendwatching", "owner": "Marketing Expert", "due": "next meeting", "source_line_ids": ["t1234567"]}
   ]}}}
   ```
   Only `text` is required. `task` is accepted for `text` (the LLM summarizer's field), and `{"ES2004a": [items]}` works too. `owner`: a display name, role, first name or label; `""` = unassigned. `due`: a date or a phrase. `source_line_ids` are only used by the synthetic edge-case breakdown.
3. Score it, and log it next to the baselines:
   ```bash
   .venv/bin/python 6_evaluation/action_items/score.py --data ami --split eval --predictions llm_ami_eval.json --name llm
   .venv/bin/python 6_evaluation/action_items/score.py --data synthetic --split test --predictions llm_synthetic_test.json --name llm
   .venv/bin/python 6_evaluation/action_items/report.py      # the tables, with the LLM's rows
   ```
   Or, from Python, with the same loaders (run from the repo root):
   ```python
   import json, sys
   for folder in ["3_action_items", "6_evaluation/action_items", "5_data/ami"]:
       sys.path.insert(0, folder)
   import data
   meetings, info = data.load("ami", "eval")     # m.lines (contract lines), m.names() (display names)
   out = {"meetings": {m.id: {"items": my_llm(m.lines, m.names())} for m in meetings}}
   json.dump(out, open("llm_ami_eval.json", "w"))
   ```

Tune the LLM's prompt on dev (`--split dev`), not on eval or test.

## 6. Results log

[`6_evaluation/action_items/results/results_log.jsonl`](../6_evaluation/action_items/results/results_log.jsonl), one line per run, like the summarization log:
- `commit` + `uncommitted_changes`;
- `data_hashes`: AMI `data_hash`, the zip's SHA-256 and the extractive links' hash; or the synthetic commit and the hash of the files read;
- `config` + `config_hash`, `model_versions` (classifier and embedder, with revisions);
- `metrics` (with intervals), `breakdowns`, `timing`;
- Python, package versions and hardware (chip, device).

`results/runs/<run_id>.json` adds every matched pair and every miss per meeting. `results/predictions/<run_id>.json` has the predicted items in the contract format.
