#!/usr/bin/env bash
# Gemini recursive eval with a 120 s update interval (llm_120s.json), on the test split of both datasets.
#   bash 2_summarization/hpo/llm_recursive.sh        (from the repo root, Git Bash)
# Every LLM call is saved as it returns (hpo/results/eval/llm_cache.jsonl). If a run stops, e.g. on the free
# tier's daily quota, this script waits and starts it again; saved calls are reused, so it resumes where it
# stopped. Gives up after 60 tries (~20 h). Log: hpo/results/llm_eval.log.
set -u
cd "$(dirname "$0")/../.."
PY=.venv/Scripts/python; [ -x "$PY" ] || PY=.venv/bin/python
export PYTHONHASHSEED=42
HPO=2_summarization/hpo
LOG=$HPO/results/llm_eval.log
for try in $(seq 1 60); do
  echo "=== $(date '+%F %T') try $try" >> "$LOG"
  $PY 2_summarization/run.py --results-dir $HPO/results/eval --model llm --split eval --mode recursive \
      --params $HPO/llm_120s.json --tag 120s --seed 42 --bertscore 2>&1 \
    | grep --line-buffered -v -i -e "warn" -e "Loading weights" -e "unauth" -e "lm_head" -e "pooler" -e "Fetching" \
    >> "$LOG"
  if [ "${PIPESTATUS[0]}" -eq 0 ]; then echo "=== $(date '+%F %T') done" >> "$LOG"; exit 0; fi
  echo "=== $(date '+%F %T') stopped (see above); saved calls: $(wc -l < $HPO/results/eval/llm_cache.jsonl 2>/dev/null || echo 0); waiting 20 min" >> "$LOG"
  sleep 1200
done
echo "=== $(date '+%F %T') gave up after 60 tries" >> "$LOG"; exit 1
