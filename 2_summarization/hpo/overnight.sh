#!/usr/bin/env bash
# The full tuning + evaluation run (several hours on a CPU). From the repo root, in Git Bash / bash:
#   bash 2_summarization/hpo/overnight.sh            (tune_textrank.py must have written textrank_best.json)
# Steps: TextRank test evals (default vs tuned, both modes) -> BART search on dev -> BART test evals.
# Every eval is logged by run.py (results_log.jsonl + runs/); the log of this script is hpo/results/overnight.log.
set -u
cd "$(dirname "$0")/../.."
PY=.venv/Scripts/python; [ -x "$PY" ] || PY=.venv/bin/python
export PYTHONHASHSEED=42
HPO=2_summarization/hpo/results
LOG=$HPO/overnight.log
mkdir -p "$HPO"
step() { echo; echo "=== $(date '+%F %T') $*"; "$@"; echo "=== $(date '+%F %T') exit $?"; }
{
  for mode in recursive full; do
    step $PY 2_summarization/run.py --results-dir $HPO/eval --model textrank --split eval --mode $mode
    step $PY 2_summarization/run.py --results-dir $HPO/eval --model textrank --split eval --mode $mode --params $HPO/textrank_best.json
  done
  step $PY 2_summarization/hpo/tune_bart.py
  for mode in recursive full; do
    step $PY 2_summarization/run.py --results-dir $HPO/eval --model bart --split eval --mode $mode --seed 42 --bertscore
    step $PY 2_summarization/run.py --results-dir $HPO/eval --model bart --split eval --mode $mode --seed 42 --bertscore --params $HPO/bart_best.json
  done
  echo "=== $(date '+%F %T') all done"
} 2>&1 | grep -v -e "^  seed " -e "unauthenticated requests" >> "$LOG"
