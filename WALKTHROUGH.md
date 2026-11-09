# Week 8 walkthrough: fixes

Fill this in **within 24 hours after the meeting** with the instructor. Commit each agreed fix on its own, then add a row with that commit's hash (`git log --oneline -1`). When every fix is in, tag the final commit and send the repo link + tag:

```bash
git tag walkthrough-v1
git push origin walkthrough-v1
```

## Fixes agreed in the meeting

| # | Fix | Commit |
|---|---|---|
| 1 | _(from the meeting)_ | _(hash)_ |

## Submission checklist (from the assignment)

- [ ] Instructor has read access to the repo (GitHub username: ______)
- [x] README states one command that runs the baseline on the smoke test ([2.1](README.md#21-run-it-smoke-test-under-1-minute-no-gpu-no-credentials))
- [x] Environment is a committed file: `.python-version` + pinned `requirements.txt`
- [x] Seeds are set in code that runs every time (top of `2_summarization/run.py`)
- [x] Data version recorded: `5_data/ami/MANIFEST.json` (source, retrieval date, hashes)
- [x] Split definition committed: `5_data/ami/ami_scenario_split.json` (AMI's official split)
- [x] Baseline result logged with commit hash, config and data hash: `6_evaluation/summarization/results/results_log.jsonl`
- [x] Smoke test runs in under 5 minutes, no GPU, no credentials (about 25 s)
- [x] No secrets, credentials or restricted data in the repo or its history
- [x] Dataset licence in the repo: `5_data/ami/LICENSE_AMI.txt`
- [ ] A teammate who didn't write the pipeline has cloned fresh and run the README command
