# LiveMinutes: project plan and layout

The parts of the old README that cover the whole project: the planned pipeline, folder layout, team conventions, and the components that are not built yet (3 to 7). The baseline walkthrough is in the [README](../README.md); the details are in [SUMMARIZATION.md](SUMMARIZATION.md) and [INGESTION.md](INGESTION.md).

## Pipeline

A meeting flows top to bottom. Each step reads what the step above it writes.

```
        Meet / Teams meeting
                │
                ▼
   ┌─────────────────────────┐
   │ 1  Ingestion            │  bot joins, asks consent, transcribes live
   └─────────────────────────┘
                │  transcript.txt   "[mm:ss] Name: text"
                ▼
   ┌─────────────────────────┐
   │ 2  Summarization        │  live summary while the meeting runs
   └─────────────────────────┘
                │
                ▼
   ┌─────────────────────────┐
   │ 3  Action items         │  task · owner · due date
   └─────────────────────────┘
                │  transcripts + summaries, indexed per meeting
                ▼
   ┌─────────────────────────┐
   │ 4  Q&A                  │  ask questions about past meetings
   └─────────────────────────┘

   Supporting:   5 Data (test meetings) · 6 Evaluation (scores 2–4) · 7 Infra (AWS)
```

| Step | In | Out | How |
|---|---|---|---|
| [1. Ingestion](INGESTION.md) | Live meeting audio | `sessions/<bot_id>/transcript.txt` | Attendee bot, consent gate, Groq Whisper |
| [2. Summarization](SUMMARIZATION.md) | Transcript | Running summary | Extractive vs abstractive, TextRank baseline |
| [3. Action items](#3-action-items) | Transcript | Tasks with owner and due date | |
| [4. Q&A](#4-qa-knowledge-base) | Past transcripts + summaries | Answers to questions | Retrieval (BM25, vector, graph, reranker) → API-hosted LLM |
| [5. Data](#5-data) | | Test meetings | AMI Meeting Corpus + synthetic meetings (LLM-written, human-reviewed) |
| [6. Evaluation](#6-evaluation) | Outputs of 2–4 | Scores | ROUGE / BERTScore (2), P/R/F1 + owner/due accuracy (3), LLM-as-judge + human review (4) |
| [7. Infra](#7-infra) | | Deployment | AWS |

## Layout

Folders are numbered in pipeline order, and each has the matching section below.

| Folder | What |
|---|---|
| [1_ingestion/](../1_ingestion/) | Attendee meeting bot → `bridge.py` → Groq Whisper. Consent gate, Meet per-speaker audio, Teams speaker labels |
| [2_summarization/](../2_summarization/) | Feature 1: live summarization |
| [3_action_items/](../3_action_items/) | Feature 2: action items |
| [4_qa/retrieval/bm25/](../4_qa/retrieval/bm25/) | F3 baseline: BM25 |
| [4_qa/retrieval/reranker/](../4_qa/retrieval/reranker/) | F3: LightGBM reranker over candidates |
| [4_qa/retrieval/vector/](../4_qa/retrieval/vector/) | F3: vector retrieval |
| [4_qa/retrieval/graph/](../4_qa/retrieval/graph/) | F3: Neo4j graph retrieval |
| [4_qa/answer/](../4_qa/answer/) | F3: answer generation (API-hosted LLM) |
| [5_data/](../5_data/) | Corpora, synthetic meetings, Q&A pairs |
| [6_evaluation/](../6_evaluation/) | Metrics + judge harness for F1–F3 |
| [7_infra/aws/](../7_infra/aws/) | AWS deployment (new-account credits) |

## Conventions

- **One short README** at the repo root: what the project is, and the baseline walkthrough from a fresh clone. Longer per-component docs live in `docs/` (this file, [INGESTION.md](INGESTION.md), [SUMMARIZATION.md](SUMMARIZATION.md)), not in per-folder READMEs.
- **Shared at the repo root:** one `requirements.txt` (Python packages for every component) and one `.env` (all secrets, copied from `.env.example`). Each component keeps its own `settings.py` (settings, committed).
- Secrets only in `.env`, never in code. Meeting audio/transcripts, `5_data/raw/` and `5_data/processed/` are gitignored. Never commit real people's voices or transcripts.
- Shared interchange format between components: the ingestion transcript line `[mm:ss] Name: text` (see [1.3 Output](INGESTION.md#13-output-what-downstream-components-read)).

## 3. Action items

Folder: [3_action_items/](../3_action_items/). Feature 2: from `1_ingestion/sessions/<bot_id>/transcript.txt`, extract each action item: **task**, **owner**, **due date**, and the line it came from.

- **Baseline:** rule-based phrase patterns.
- **Compare:** rules vs LLM extraction.
- **Metrics:** item precision / recall / F1, owner and due-date accuracy ([6](#6-evaluation)).
- **Data:** gold action items per meeting in `5_data/synthetic/action_items/`.

## 4. Q&A (knowledge base)

Folder: [4_qa/](../4_qa/). Feature 3: question → retrieve chunks from past meeting transcripts → API-hosted answer LLM → answer with cited meeting/time.

| Approach | Folder | What |
|---|---|---|
| BM25 (baseline) | `4_qa/retrieval/bm25/` | Lexical baseline over transcript chunks. Also the candidate generator for the reranker |
| LightGBM reranker | `4_qa/retrieval/reranker/` | Reranks top-k candidates (e.g. from BM25) with LightGBM learning-to-rank (lambdarank). Features: BM25 score, term overlap, speaker/time metadata |
| Vector retrieval | `4_qa/retrieval/vector/` | Dense embeddings over transcript chunks |
| Neo4j graph | `4_qa/retrieval/graph/` | Meetings, speakers, topics and utterances as a graph in Neo4j |
| Answer generation | `4_qa/answer/` | Takes retrieved chunks and calls an API-hosted LLM to write the answer, citing meeting and timestamp |

**Shared retriever contract** (so every row of the comparison table runs through the same evaluator):
`retrieve(question: str, k: int) -> list[{"meeting_id", "chunk_id", "text", "score"}]`

Evaluation: LLM-generated, manually validated Q&A pairs (`5_data/synthetic/qa/`). Correctness is scored by LLM-as-judge plus human review (`6_evaluation/qa/`).

## 5. Data

Folder: [5_data/](../5_data/).

| Folder | Contents | In git? |
|---|---|---|
| `ami/` | AMI scripts: download + hash check (`fetch_data.py`), split (`split.py`), reader (`ami.py`), manifest, licence, split file. See [2](SUMMARIZATION.md) | Yes |
| `raw/` | AMI Meeting Corpus downloads (transcripts, summaries, annotations) | No |
| `processed/` | AMI converted to the shared transcript format `[mm:ss] Name: text`, plus chunked versions for retrieval | No |
| `synthetic/meetings/` | LLM-generated edge-case meetings (AMI is too clean), each human-reviewed | Yes, once reviewed |
| `synthetic/action_items/` | Gold action items per meeting, `<meeting>.gold.json` | Yes, once reviewed |
| `synthetic/qa/` | LLM-generated Q&A pairs, manually validated | Yes, once validated |

Rules:
- Every synthetic item records: generator model + prompt, reviewer, review date, accept/reject.
- Keep fixed train/dev/test splits by meeting ID, so nothing leaks between reranker training and evaluation.
- Real recorded meetings (from `1_ingestion/sessions/`) never go in here without everyone's consent.

## 6. Evaluation

Folder: [6_evaluation/](../6_evaluation/).

- `summarization/`: ROUGE + BERTScore for Feature 1 (`metrics.py`), and the results log (`results/results_log.jsonl`). See [2](SUMMARIZATION.md).
- Feature 2 (action items): item P/R/F1, owner and due accuracy (harness to be added).
- `qa/`: retrieval metrics (Recall@k, MRR, nDCG) per retriever, then answer correctness via LLM-as-judge + human review.

Every approach is run through the same script on the same split, and the results go into one comparison table.

## 7. Infra

Folder: [7_infra/aws/](../7_infra/aws/). AWS deployment on new-account free credits. Things to plan for, from ingestion:

- Each Attendee bot runs a Chrome (~1 CPU, 1–2 GB RAM). Local mode runs all bots in one worker; for production, run one bot per container/pod.
- The bridge needs a real TLS cert for `wss://` (replacing `make_certs.sh`).
- The bridge already ships as an image (`1_ingestion/Dockerfile`, `liveminutes-bridge`). Push it to ECR and run it next to the Attendee worker; `sessions/` must be a persistent volume (or move to S3).
- Secrets go in AWS Secrets Manager / SSM, never in the image.

## Origin

`1_ingestion/` is the `attendee-groq-bridge/` system from [aatmaj28/ai-capstone](https://github.com/aatmaj28/ai-capstone) (commit `1854b3b`), since extended here (Docker image, consent vouching, transcript merging). The Vexa prototype from that repo was not carried over. `2_summarization/`, `5_data/ami/` and `6_evaluation/summarization/` come from that repo's `f1-summarization-baseline` branch, extended here with multi-seed runs.
