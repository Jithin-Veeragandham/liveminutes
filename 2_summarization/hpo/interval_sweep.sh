#!/usr/bin/env bash
# Even comparison across update intervals: every model in recursive mode on the test split of both datasets, at
# the SAME intervals, changing only the interval (the other settings stay each model's tuned ones).
#   bash 2_summarization/hpo/interval_sweep.sh textrank   # seconds
#   bash 2_summarization/hpo/interval_sweep.sh bart       # ~1.5 h per interval on a CPU
#   bash 2_summarization/hpo/interval_sweep.sh llm        # Gemini: 1535 / 813 / 428 / 293 / 182 calls at 30..300 s
# Runs are tagged every<N>s (e.g. textrank_every60s_ami_eval_recursive_1seeds) in hpo/results/eval. The LLM part
# reuses saved calls (eval/llm_cache.jsonl) and retries every hour until done, so it survives the free tier's
# daily quota (500 calls / day: ~6 days for all intervals, a few hours with billing).
set -u
cd "$(dirname "$0")/../.."
PY=.venv/Scripts/python; [ -x "$PY" ] || PY=.venv/bin/python
export PYTHONHASHSEED=42
HPO=2_summarization/hpo
P=$HPO/results/interval_params; mkdir -p $P
LOG=$HPO/results/interval_sweep_$1${LANE:+_$LANE}.log
INTERVALS="${INTERVALS:-300 180 120 60 30}"   # cheapest first; INTERVALS="60 30" LANE=a to run a subset in parallel

params() {   # $1 model, $2 seconds -> params file: the model's tuned settings with only the interval changed
  $PY - "$1" "$2" "$P/$1_$2.json" <<'PYEOF'
import json, sys
model, sec, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
base = {"textrank": json.load(open("2_summarization/hpo/results/textrank_best.json"))["recursive"],
        "bart": json.load(open("2_summarization/hpo/results/bart_best.json"))["recursive"],
        "llm": {"min_seconds_between_calls": 5}}[model]
key = {"textrank": "chunk_seconds", "bart": "window_seconds", "llm": "chunk_seconds"}[model]
json.dump({"recursive": dict(base, **{key: sec})}, open(out, "w"), indent=1)
PYEOF
}

run() {   # $1 model, $2 seconds
  params $1 $2
  $PY 2_summarization/run.py --results-dir $HPO/results/eval --model $1 --split eval --mode recursive \
      --params $P/$1_$2.json --tag every$2s --seed 42 --bertscore 2>&1 \
    | grep --line-buffered -v -i -e "warn" -e "Loading weights" -e "unauth" -e "lm_head" -e "pooler" -e "Fetching" >> "$LOG"
  return ${PIPESTATUS[0]}
}

for s in $INTERVALS; do
  echo "=== $(date '+%F %T') $1 every $s s" >> "$LOG"
  if [ "$1" = llm ]; then
    for try in $(seq 1 200); do
      run llm $s && break
      echo "=== $(date '+%F %T') stopped (quota?); saved calls: $(wc -l < $HPO/results/eval/llm_cache.jsonl); retry in 1 h" >> "$LOG"
      sleep 3600
    done
  else
    run $1 $s
  fi
done
echo "=== $(date '+%F %T') all done" >> "$LOG"
