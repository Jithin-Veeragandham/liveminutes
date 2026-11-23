# Gemini summarizer: spread across seeds (eval split)

Model `gemini-3.5-flash-lite`, `2_summarization/run.py --model llm --split eval`, one run per seed. Each seed makes its own calls (its own call cache); Gemini is called at its default sampling settings (no temperature or seed is sent), so the spread is the model's run-to-run variation. Mean ± standard deviation (n−1) across the seeds listed; n = meetings per run.

## full

| Dataset | Seeds | n | ROUGE-1 | ROUGE-2 | ROUGE-L | BERTScore F1 |
|---|---|---|---|---|---|---|
| AMI | 42, 43, 44, 45, 46 | 20 | 0.3829 ± 0.0096 | 0.0935 ± 0.0057 | 0.2167 ± 0.0087 | 0.8678 ± 0.0012 |
| Synthetic | 42, 43, 44, 45, 46 | 20 | 0.4345 ± 0.0098 | 0.1724 ± 0.0064 | 0.2858 ± 0.0031 | 0.8743 ± 0.0006 |

## live 120 s

| Dataset | Seeds | n | ROUGE-1 | ROUGE-2 | ROUGE-L | BERTScore F1 |
|---|---|---|---|---|---|---|
| AMI | 42, 43, 44, 45, 46 | 20 | 0.3049 ± 0.0084 | 0.0715 ± 0.0021 | 0.1725 ± 0.0056 | 0.8594 ± 0.0008 |
| Synthetic | 42, 43, 44, 45, 46 | 20 | 0.3750 ± 0.0154 | 0.1400 ± 0.0085 | 0.2399 ± 0.0052 | 0.8662 ± 0.0009 |

## Per seed

| Mode | Dataset | Seed | ROUGE-1 | ROUGE-2 | ROUGE-L | BERTScore F1 | Calls | Run |
|---|---|---|---|---|---|---|---|---|
| full | ami | 42 | 0.3894 | 0.0933 | 0.2173 | 0.8686 | 20 (+0 from cache) | 20261120T181703Z_llm_ami_eval_full_1seeds |
| full | ami | 43 | 0.3770 | 0.0942 | 0.2094 | 0.8674 | 19 (+1 from cache) | 20261121T204500Z_llm_ami_eval_full_1seeds |
| full | ami | 44 | 0.3945 | 0.1010 | 0.2266 | 0.8691 | 20 (+0 from cache) | 20261121T210428Z_llm_ami_eval_full_1seeds |
| full | ami | 45 | 0.3831 | 0.0939 | 0.2236 | 0.8679 | 20 (+0 from cache) | 20261121T212348Z_llm_ami_eval_full_1seeds |
| full | ami | 46 | 0.3704 | 0.0850 | 0.2064 | 0.8660 | 20 (+0 from cache) | 20261121T214306Z_llm_ami_eval_full_1seeds |
| full | synthetic | 42 | 0.4454 | 0.1805 | 0.2870 | 0.8751 | 20 (+0 from cache) | 20261120T182055Z_llm_synthetic_eval_full_1seeds |
| full | synthetic | 43 | 0.4190 | 0.1650 | 0.2807 | 0.8734 | 20 (+0 from cache) | 20261121T205446Z_llm_synthetic_eval_full_1seeds |
| full | synthetic | 44 | 0.4377 | 0.1727 | 0.2890 | 0.8743 | 20 (+0 from cache) | 20261121T211410Z_llm_synthetic_eval_full_1seeds |
| full | synthetic | 45 | 0.4327 | 0.1672 | 0.2856 | 0.8741 | 20 (+0 from cache) | 20261121T213328Z_llm_synthetic_eval_full_1seeds |
| full | synthetic | 46 | 0.4376 | 0.1766 | 0.2869 | 0.8745 | 20 (+0 from cache) | 20261121T215245Z_llm_synthetic_eval_full_1seeds |
| live 120 s | ami | 42 | 0.3050 | 0.0710 | 0.1791 | 0.8592 | 210 (+124 from cache) | 20261121T090614Z_llm_120s_ami_eval_recursive_1seeds |
| live 120 s | ami | 43 | 0.2911 | 0.0698 | 0.1649 | 0.8586 | 334 (+0 from cache) | 20261121T222040Z_llm_120s_ami_eval_recursive_1seeds |
| live 120 s | ami | 44 | 0.3116 | 0.0713 | 0.1712 | 0.8605 | 334 (+0 from cache) | 20261121T225630Z_llm_120s_ami_eval_recursive_1seeds |
| live 120 s | ami | 45 | 0.3117 | 0.0751 | 0.1768 | 0.8599 | 334 (+0 from cache) | 20261121T233220Z_llm_120s_ami_eval_recursive_1seeds |
| live 120 s | ami | 46 | 0.3050 | 0.0705 | 0.1703 | 0.8588 | 334 (+0 from cache) | 20261122T000816Z_llm_120s_ami_eval_recursive_1seeds |
| live 120 s | synthetic | 42 | 0.3811 | 0.1403 | 0.2425 | 0.8662 | 94 (+0 from cache) | 20261121T091951Z_llm_120s_synthetic_eval_recursive_1seeds |
| live 120 s | synthetic | 43 | 0.3489 | 0.1255 | 0.2317 | 0.8658 | 94 (+0 from cache) | 20261121T222836Z_llm_120s_synthetic_eval_recursive_1seeds |
| live 120 s | synthetic | 44 | 0.3888 | 0.1434 | 0.2409 | 0.8676 | 94 (+0 from cache) | 20261121T230425Z_llm_120s_synthetic_eval_recursive_1seeds |
| live 120 s | synthetic | 45 | 0.3810 | 0.1433 | 0.2454 | 0.8659 | 94 (+0 from cache) | 20261121T234018Z_llm_120s_synthetic_eval_recursive_1seeds |
| live 120 s | synthetic | 46 | 0.3754 | 0.1473 | 0.2389 | 0.8653 | 94 (+0 from cache) | 20261122T001611Z_llm_120s_synthetic_eval_recursive_1seeds |

## This overnight run (seeds 43-46)

- Calls to Gemini: 1871 (not counting answers reused from the cache when a run resumed)
- Tokens: 4,853,473 in, 268,783 out
- Cost: about $2.13 at $0.3/1M input and $2.5/1M output tokens (list price; check the billing report for the exact amount)
- Time: 2026-11-21T15:35:18 to 2026-11-21T19:16:11 (221 min)
- Seed 42 runs are Jithin's (logged earlier on eval-limits); seeds 43-46 are new.
