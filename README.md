# LiveMinutes

CS6170 AI Capstone, Northeastern. This README covers the **summarization baseline**, from a fresh clone to the logged number. Details are in [`docs/`](docs/): [SUMMARIZATION.md](docs/SUMMARIZATION.md), [INGESTION.md](docs/INGESTION.md) (the meeting bot) and [PROJECT.md](docs/PROJECT.md) (layout, plans for Features 2 and 3).

## 1. What LiveMinutes is

- **Meeting bot** ([`1_ingestion/`](1_ingestion/)): the open-source [Attendee](https://github.com/attendee-labs/attendee) bot joins the call. Our `bridge.py` keeps no audio until everyone has said yes in chat, then transcribes with Groq Whisper.
- **Summarization** ([`2_summarization/`](2_summarization/)): turns a meeting transcript into a short summary. We measure it on the AMI meeting corpus against human-written summaries.
- **Not built yet:** action items (`3_action_items/`), Q&A over past meetings (`4_qa/`), AWS deployment (`7_infra/`).

## 2. Data

| | |
|---|---|
| Corpus | [AMI Meeting Corpus](https://groups.inf.ed.ac.uk/ami/corpus/), manual annotations: transcripts (words + dialogue acts), human-written summaries, speaker info |
| Source | `https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip` (23 MB, public, no account) |
| License | **CC BY 4.0**. Full text in [`5_data/ami/LICENSE_AMI.txt`](5_data/ami/LICENSE_AMI.txt). Credit: the AMI Consortium. Carletta et al., *The AMI Meeting Corpus: A Pre-announcement*, MLMI 2005. We don't change the files; our code only reads them. |
| How it's downloaded | Automatically, the first time you run the baseline (or with `.venv/bin/python 5_data/ami/fetch_data.py`). It goes to `5_data/raw/ami/`, which is gitignored and never committed. |
| Manifest | [`5_data/ami/MANIFEST.json`](5_data/ami/MANIFEST.json): URL, retrieval date (2026-11-07), size, the zip's SHA-256, and one `data_hash` over every extracted file we use. **Every run checks both hashes and stops if either is different.** |
| Split | AMI's official scenario-only split, **by team**: each team of 4 people recorded 4 meetings (a to d), and a team's meetings all go to one split, so no person is in both train and test. 98 train / 20 dev / 20 eval meetings. Smoke test = 3 dev meetings. Rule: [`5_data/ami/split.py`](5_data/ami/split.py) (`THE SPLIT IS MADE HERE`). Result: [`5_data/ami/ami_scenario_split.json`](5_data/ami/ami_scenario_split.json). |

## 3. Baseline and metric

| | What | Why |
|---|---|---|
| **Baseline: TextRank** ([Mihalcea & Tarau 2004](https://aclanthology.org/W04-3252/); [`textrank_baseline.py`](2_summarization/textrank_baseline.py), `summa` 1.2.0) | Builds a graph of transcript lines linked by shared words, ranks them with PageRank, and keeps the top lines (in meeting order) up to 160 words. 160 = the average length of the **train** summaries. | No training (nothing can leak, no GPU). Fast. Only copies real lines, so it can't make things up. A standard published AMI baseline, so we can compare. |
| **Deep model: BART** ([`bart_summarizer.py`](2_summarization/bart_summarizer.py), `philschmid/bart-large-cnn-samsum`, pinned revision) | Summarizes every 60 s of meeting, then summarizes those summaries. | Writes new sentences, like a person would. Trained on chat summaries (SAMSum), never on AMI. **Not logged yet.** |
| **LLM: rolling notes** ([`llm_summarizer.py`](2_summarization/llm_summarizer.py), Claude `claude-sonnet-5-5` by default, provider-agnostic via [`llm_providers.py`](2_summarization/llm_providers.py)) | Sends the transcript ~30 s at a time (at most one call per 30 s), each time with the notes so far, and gets back updated notes as structured JSON: a summary plus action items (task, owner's name if said, due date). Run: `run.py --model llm` (AMI, scored) or `llm_summarizer.py <transcript.txt>` (a live meeting). Needs `ANTHROPIC_API_KEY` (Claude) or `GEMINI_API_KEY` (Gemini) in `.env`. | Shows what an LLM adds over TextRank and BART, and produces the action items. The rolling setup is how notes would update during a live meeting. Details: [docs/SUMMARIZATION.md 2.12](docs/SUMMARIZATION.md#212-llm-rolling-notes-summary--action-items). **Not logged yet.** |
| **Metric: ROUGE-1 / 2 / L** (+ ROUGE-SU4) ([`metrics.py`](6_evaluation/summarization/metrics.py), Google `rouge-score` 0.1.2, stemming on) | Word overlap between our summary and AMI's human summary. F-measure, 0 to 1, averaged over meetings. | The standard metric in AMI summarization papers, so our numbers are comparable. Cheap and deterministic. |
| **Metric: BERTScore** (`bert-score` 0.3.13, `roberta-large`, pinned revision) | Meaning overlap, using a language model's embeddings. | ROUGE gives no credit for paraphrases. BERTScore does, which is fair to BART. |

**Baseline result** (TextRank, 20 seeds, logged at commit `a43e185`):

| Split | ROUGE-1 | ROUGE-2 | ROUGE-L | Std over seeds | Time per meeting |
|---|---|---|---|---|---|
| smoke (3 dev meetings) | 0.260 | 0.040 | 0.150 | 0 | 0.05 s |
| **eval (20 test meetings)** | **0.238** | **0.027** | **0.129** | 0 | 0.2 s |

TextRank has no randomness, so every seed gives the same summary (std 0). The seeds start to matter for BART and later models. The published TextRank ROUGE-1 on AMI is 35.25 ([Zhu et al. 2020](https://aclanthology.org/2020.findings-emnlp.19/), Table 2). Possible reasons ours is lower (not checked yet): we compare against the abstract part of the human summary only, we use a 160-word budget, and the ROUGE implementation differs.

## 4. Quick start: fresh clone to the smoke-test number

**You need:** macOS, Linux or Windows, `git`, internet access, about 1.1 GB of disk, and [uv](https://docs.astral.sh/uv/getting-started/installation/) (check with `uv --version`). Install uv with `brew install uv` (macOS), `curl -LsSf https://astral.sh/uv/install.sh | sh` (Linux) or `winget install astral-sh.uv` (Windows, then open a new terminal). uv downloads **Python 3.12** for you if you don't have it. Any 3.12.x works; the logged runs used 3.12.16. **No GPU, no accounts, no API keys.**
Tested on macOS 27 (Apple M5) with uv 0.12, and on Windows 11 (PowerShell, Python 3.12.10): same scores and hashes.
`requirements.txt` includes torch for the optional BART model, so the install downloads about 900 MB on macOS. On Linux x86_64 it's about 3 GB, because torch also pulls in NVIDIA GPU libraries, which `requirements.txt` doesn't pin and the baseline doesn't use.

**macOS / Linux** (Terminal):

```bash
git clone https://github.com/Jithin-Veeragandham/liveminutes.git
cd liveminutes
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

**Windows** (PowerShell):

```powershell
git clone https://github.com/Jithin-Veeragandham/liveminutes.git
cd liveminutes
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
```

The only difference is where the environment keeps Python: `.venv/bin/python` on macOS/Linux, `.venv\Scripts\python` on Windows. **Every command in this README uses `.venv/bin/python`: on Windows, type `.venv\Scripts\python` instead.** There's no need to activate the environment (uv suggests it): every command calls that Python directly. On Windows, keep the repo outside a OneDrive folder; installs there are much slower.

**The one command** (runs the baseline on the smoke-test subset):

macOS / Linux:

```bash
.venv/bin/python 2_summarization/run.py --model textrank --split smoke
```

Windows (PowerShell):

```powershell
$env:PYTHONHASHSEED = "42"
.venv\Scripts\python 2_summarization\run.py --model textrank --split smoke
```

First run: 30 to 40 s, mostly the 23 MB download. Later runs: about 3 s. It downloads AMI and checks its hashes, checks the split, then summarizes the 3 smoke meetings once per seed (20 seeds) and scores them. You should see:

```
downloading https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip (23 MB) ...
AMI data OK  (data_hash 1ce0450de10c...)
split OK; smoke: 3 meetings
seeds: 20 (42..61)
  seed 42: rouge1 0.2600  rouge2 0.0399  rougeL 0.1498  rougeSU4 0.0757
  ... (the same line for seeds 43 to 61)

mean over 20 seeds:  rouge1 0.26 (±0.0)  rouge2 0.0399 (±0.0)  rougeL 0.1498 (±0.0)  rougeSU4 0.0757 (±0.0)
seconds_per_meeting 0.04  identical_across_seeds True
seed variation: Nothing changed: all 20 seeds produced identical summaries for all 3 meetings, so every metric has std 0.
  rouge1       std 0.0  min 0.26 (seed 42)  max 0.26 (seed 42)  range 0.0
  ... (the same line for rouge2, rougeL, rougeSU4)
logged <run_id>  commit <current commit>  data 1ce0450de10c  config f92e56918eb9
```

`seconds_per_meeting`, `<run_id>` and the commit will differ on your machine. The four scores, the data hash and the config hash must match exactly.

**Run it again:** you get the same numbers (no download this time). To compare your runs with the committed ones, print every smoke line in the results log:

macOS / Linux:

```bash
grep '"split": "smoke"' 6_evaluation/summarization/results/results_log.jsonl | .venv/bin/python -c "import sys, json; [print(d['run_id'], d['commit'][:7], d['metrics_mean']) for d in map(json.loads, sys.stdin)]"
```

Windows (PowerShell):

```powershell
.venv\Scripts\python -c "import json; [print(d['run_id'], d['commit'][:7], d['metrics_mean']) for d in map(json.loads, open('6_evaluation/summarization/results/results_log.jsonl')) if d['split'] == 'smoke']"
```

Good to know:
- **Each run adds a line to `results_log.jsonl` and a file in `results/runs/`**, which are committed files, so `git status` shows them afterwards. Add `--no-log` for a run you don't want to keep. To undo every run you haven't committed:
  `git checkout -- 6_evaluation/summarization/results/results_log.jsonl && git clean -f 6_evaluation/summarization/results/runs/`
  (`git clean` there only deletes run files that aren't committed.)
- The script **restarts itself once** at the start, to fix Python's hash seed (`PYTHONHASHSEED`). That's expected. On Windows a restart starts a new process, so its output can appear after the prompt returns; setting `$env:PYTHONHASHSEED = "42"` first (as above) avoids the restart. It lasts until you close that PowerShell window.
- Undo command on Windows: the same two `git` commands, run one after the other (Windows PowerShell has no `&&`).
- If the download fails, check you can reach `groups.inf.ed.ac.uk`. If you delete `5_data/raw/ami/`, the next run downloads the same files again and checks them against the manifest.

## 5. Full run

From the repo root, after the quick start. Times measured on the macOS machine above. **Windows:** type `.venv\Scripts\python` in place of `.venv/bin/python` (and `\` in paths is fine too).

| Stage | Command | Time |
|---|---|---|
| Set up the environment | `uv venv` + `uv pip install` (quick start) | about 5 s here from an empty cache: a 900 MB download, mostly torch for BART (about 3 GB on Linux x86_64), so longer on a slow connection |
| Download + check data (first run only) | runs automatically, or `.venv/bin/python 5_data/ami/fetch_data.py` | 10 to 30 s (network) |
| Baseline, smoke (3 meetings × 20 seeds) | `.venv/bin/python 2_summarization/run.py --model textrank --split smoke` | about 3 s |
| **Baseline, eval (20 test meetings × 20 seeds)** | `.venv/bin/python 2_summarization/run.py --model textrank --split eval` | about 70 s |
| Baseline, dev (20 meetings × 20 seeds) | `.venv/bin/python 2_summarization/run.py --model textrank --split dev` | about 65 s |
| Optional: BART + BERTScore | `.venv/bin/python 2_summarization/run.py --model bart --split smoke --seeds 3 --bertscore` | about 8 min the first time: a one-time download of about 4.5 GB of models, then about 30 s per meeting per seed on CPU |

Useful flags: `--seeds 5` (5 seeds), `--seed 7` (exactly one seed), `--no-log`. Checks on their own: `.venv/bin/python 5_data/ami/fetch_data.py --verify` (data) and `.venv/bin/python 5_data/ami/split.py --check` (split). More in [docs/SUMMARIZATION.md](docs/SUMMARIZATION.md).

## 6. Where things are recorded

| What | Where |
|---|---|
| **Environment** | [`.python-version`](.python-version) (3.12), [`requirements.txt`](requirements.txt) (one file: ingestion, baseline, and BART + BERTScore, summarization packages pinned with `==`). Each run also logs its Python version, package versions and hardware. |
| **Seeds** | Top of [`2_summarization/run.py`](2_summarization/run.py): `N_SEEDS = 20`, `FIRST_SEED = 42`. Python `random`, NumPy and torch are seeded again for every seed, and `PYTHONHASHSEED` is fixed. Each run records what changed between seeds and why (`seed_variation`, see [docs/SUMMARIZATION.md](docs/SUMMARIZATION.md#22-seeds)). |
| **Data version + hashes** | [`5_data/ami/MANIFEST.json`](5_data/ami/MANIFEST.json): URL, retrieval date, zip SHA-256 `b56e5bab…`, `data_hash` `1ce0450de10c…`. Checked on every run by [`fetch_data.py`](5_data/ami/fetch_data.py). |
| **Split** | Rule in [`5_data/ami/split.py`](5_data/ami/split.py), result in [`ami_scenario_split.json`](5_data/ami/ami_scenario_split.json). Paths and hashes are also in [`MANIFEST.json`](5_data/ami/MANIFEST.json) under `split` (the file's SHA-256 `09e6d3ee…`, and one SHA-256 per train/dev/eval meeting list). Every run rebuilds the split, compares it with the file and the manifest, and checks that no person or meeting is in two splits. Each log line records the split file's SHA-256 (`split_file_sha256`). |
| **Results log** | [`6_evaluation/summarization/results/results_log.jsonl`](6_evaluation/summarization/results/results_log.jsonl), one line per run: the scores (mean, std and per seed), `commit` + `uncommitted_changes`, `config` + `config_hash`, `data_hash`, `split_file_sha256`, the meetings, the seeds, Python, packages and hardware. `results/runs/<run_id>.json` has the same plus every summary written. |

The current baseline is lines 5 and 6 of the log (smoke and eval, 20 seeds). They were made at commit `a43e185` with no uncommitted changes, `data_hash 1ce0450de10c…`, `config_hash f92e56918eb9`, on arm64 macOS with 14 CPUs and Python 3.12.16. Line 7 is the eval split again with one seed (42), at commit `fb7907f` (same code), and has the same scores. Its `config_hash` (`692b39f49764`) differs only because the seed list is part of the config.

## 7. Meeting bot (optional demo)

Not needed for the baseline. It needs Docker with Compose (Docker Desktop, or Colima on macOS), about 15 GB of disk, and a free [Groq API key](https://console.groq.com/keys) for speech-to-text. The Groq key goes in `.env` at the repo root. That file is gitignored: never commit it. Run everything from `1_ingestion/`:

```bash
cd 1_ingestion
dc() { (cd attendee && docker compose -f dev.docker-compose.yaml -f ../compose.local.yaml "$@"); }

# once per machine (the first build takes 10-20 min)
git clone -c core.autocrlf=false https://github.com/attendee-labs/attendee.git attendee
git -C attendee checkout eaacec16f2939b595f196b3957a717bbb0ed23e9
git -C attendee apply ../patches/teams-speaker-clock.patch
[ -f ../.env ] || cp ../.env.example ../.env    # must exist before any dc command (compose.local.yaml reads it)
grep -v '^ATTENDEE_API_KEY=' ../.env > ../.env.tmp && mv ../.env.tmp ../.env
dc build
dc run --rm -T attendee-app-local python init_env.py > attendee/.env
bash make_certs.sh
dc up -d postgres redis attendee-app-local attendee-worker-local attendee-scheduler-local
dc exec -T attendee-app-local python manage.py migrate
bash make_account.sh you@example.com      # writes ATTENDEE_API_KEY to ../.env, prints a local login
```

Then open `../.env` in an editor and set `GROQ_API_KEY=gsk_...` (your key). Start everything and check:

```bash
dc up -d                                   # starts all 6 services, including postgres and redis
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/accounts/login/    # -> 200 (can take 30 to 60 s)
dc ps                                      # 6 services Up; liveminutes-bridge (healthy) after about 30 s
curl http://127.0.0.1:8776/health          # -> ok
```

**Demo:** open http://localhost:8776/live, press **Send bot to a meeting**, paste a Google Meet link and press **Send bot**. Admit "LiveMinutes" from the lobby (it takes 20 to 60 s), and everyone types `yes` in the chat. The transcript then appears live. **Stop:** `dc down` (keeps the database and transcripts). **Start again later:** `dc up -d`.

**Participants' live view.** Once everyone has said yes, the bot posts one chat message with a link and a 6-character access code.
- **Joining:** participants open the link, type the code and pick their name. That's name selection, not a login.
- **What they can do:** follow the live transcript, summary and action items; edit and approve them (every change goes into an append-only edit log); approve and export the minutes.
- **Organizer:** your own view of a meeting is on http://localhost:8776/live (click the meeting). The old pages are still there: add `?classic=1`.

**Share outside this computer** with a free Cloudflare quick tunnel (no account). Without it, links point to localhost and only open on this computer; the organizer view says so. Through the tunnel, only the participant pages answer. Quick tunnels don't pass the live stream on, so pages opened through one update by polling ("Live · polling" in the header).

```bash
brew install cloudflared      # macOS; other systems: docs/UI.md
bash tunnel.sh                # starts `cloudflared tunnel --url http://localhost:8776` and gives its address to the bridge
```

Or put `PUBLIC_BASE_URL=https://...` in `../.env` and run `dc up -d`.

**Demo mode** needs no meeting and no Groq. It plays a scripted, invented 5-minute product meeting through the real consent flow, with a late joiner, a plan change, summary updates and two action items. Start it with **Start demo meeting** on http://localhost:8776/live, or:

```bash
curl -s -X POST "http://localhost:8776/live/api/demo?wait=1"    # prints the organizer link, the participant link and the code
```

Add `&speed=5` to run 5× faster. Demo meetings are folders named `sessions/demo_*`; delete them any time.

The full guide (features, keyboard shortcuts, tests, security, limits) is in [docs/UI.md](docs/UI.md). Plugging the LLM summary and the knowledge base into it: [docs/INTEGRATION.md](docs/INTEGRATION.md).

Notes:
- **Windows:** run this whole section in **Git Bash** (installed with Git for Windows), not PowerShell: the commands then work exactly as written, including `dc up -d`, the `curl` checks and `dc restart attendee-app-local`. In PowerShell, `bash` starts WSL instead of Git Bash, and `>` writes files in UTF-16, which breaks `attendee/.env`. Start Docker Desktop first.
- Run the scripts with `bash` (they aren't marked executable in git).
- The repo's `.env` must exist before the first `dc` command: `compose.local.yaml` loads it for the bridge, and `docker compose exec` (the `migrate` step) fails with `env file .../.env not found` without it.
- The `grep` line lets `make_account.sh` write the key on macOS. Its `sed -i` only works with GNU sed.
- Port 8000 (Attendee) must be free.
- If the page says 'Attendee is not reachable', run `dc restart attendee-app-local` and wait for 200 again. It happens if the database wasn't up when Attendee started.
- Attendee's database volume is shared by every clone on a computer. If you set Attendee up before in another clone, copy that clone's `attendee/.env` here instead of running `init_env.py`.

Full guide (consent rules, settings, troubleshooting): [docs/INGESTION.md](docs/INGESTION.md). Code comments that say "README section 1.x" mean section 1.x of that file.
