#!/usr/bin/env bash
# Test-split evals of TextRank with summaries capped at the human length (results/textrank_best_capped.json),
# both modes, then the update-interval sweep (30-300 s) with the capped live settings. From the repo root:
#   bash 2_summarization/hpo/textrank_capped_eval.sh
set -u
cd "$(dirname "$0")/../.."
PY=.venv/Scripts/python; [ -x "$PY" ] || PY=.venv/bin/python
export PYTHONHASHSEED=42
H=2_summarization/hpo/results
run() { echo "=== $(date '+%F %T') $*"; $PY 2_summarization/run.py --results-dir $H/eval --model textrank --split eval --seed 42 --bertscore "$@"; echo "=== exit $?"; }
{
  for mode in full recursive; do run --mode $mode --params $H/textrank_best_capped.json --tag capped; done
  for iv in 30 60 120 180 300; do run --mode recursive --params $H/interval_params/textrank_capped_$iv.json --tag capped_every${iv}s; done
  echo "=== $(date '+%F %T') all done"
} 2>&1 | grep -v -e "^  seed " -e "unauthenticated requests" >> $H/textrank_capped_eval.log
