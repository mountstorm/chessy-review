# ♞ Chessy

Free, open-source chess game review: streaming Stockfish analysis, a what-if explorer, and a coach that
**checks the engine before it speaks**. FastAPI backend, zero-dependency browser UI.

## Quick start

```bash
./setup.sh --test          # venv + deps + Stockfish check/install + .env + test suite
.venv/bin/uvicorn app.main:app --port 8765 --reload
# open http://localhost:8765
```

Manual route: Python 3.11+, `pip install -r requirements.txt -r requirements-llm.txt` (the second file is only needed for the local/Anthropic/OpenAI coaches), `brew install stockfish` (or `sudo apt install stockfish`),
`cp .env.example .env`. Without an API key the coach runs in **offline mode** (answers built purely from engine output).

### Coach options (none requires a paid API)
| Mode | Setup | Notes |
|---|---|---|
| **offline** (default) | nothing | Answers assembled from engine output. Always accurate, a bit robotic. |
| **local** | `./setup_local_llm.sh` | A small open model via [Ollama](https://ollama.com) (default `qwen2.5:3b`, ~2 GB) puts the engine's facts into friendly words. Free, private, ~1-2 s replies on Apple Silicon. Auto-detected when Ollama is running. |
| anthropic / openai | `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` in `.env` | Strongest wording and free-form tool use. Paid, sends positions to the provider. |

Note that Stockfish itself is already a neural network (NNUE) running locally; the choice above only affects how the coach *talks*.

**Why local mode is built the way it is:** a 3B model is poor at chess. In testing it ignored the engine's `Qxf7#` and made
up a pawn capture. So in local mode the model never calculates: the engine's facts are written first, the model only
rephrases them to answer your question, and if its text contains a move that isn't in the facts, the engine's own
explanation replaces it. Hints and quizzes stay fully deterministic (a model could leak the answer).

## What it does

| Feature | Endpoint |
|---|---|
| Import PGN text, Chess.com username, or Lichess username. Variation trees are kept. | `POST /api/games/import` |
| Streaming review, move by move (SSE), MultiPV 3 by default | `POST /api/games/{id}/analyze` |
| Stored review / replay | `GET /api/games/{id}/review` |
| What-if explorer: any FEN + moves → MultiPV lines, win %, motifs, overlays | `POST /api/positions/evaluate` |
| Engine-free legal moves (powers click-to-move) | `GET /api/positions/legal?fen=` |
| Grounded AI coach (SSE) with `explain`, `socratic_hint`, `quiz_me` | `POST /api/coach/chat` |
| Where a game leaves master theory + closest master games (Lichess) | `GET /api/games/{id}/masters` |
| Habits across games | `GET /api/players/{username}/insights` |
| Health | `GET /api/health` |

Interactive API docs: `http://localhost:8765/docs`.

### Move classification
Based on **expected-points (win-probability) loss**, not raw centipawns: Brilliant (sound sacrifice), Great (only move),
Best, Excellent, Good, Inaccuracy, Mistake, Blunder, Missed Win. Thresholds are documented in `app/engine/classifier.py`.

### Per-move UI metadata
Best-move / played / threat arrows (threat = opponent's best move if you passed), hanging squares, king safety and
mobility scores, a 64-square attack-control heatmap, tactical motifs (pins, forks, skewers, discovered attacks, hanging
pieces) both present and created/missed by the move, critical-moment flags (`big_swing`, `turning_point`, `only_move`).

### SSE contract
`/analyze` emits `start` → `move`×N → `summary` → `done` (or `error`). `/coach/chat` emits `grounding` → `text`* /
`tool` / `tool_result` / `ui` → `done`. A `ui` event looks like
`{"draw_arrows":[{"from":"e2","to":"e4","color":"green"}],"highlight_squares":["f7"]}`.

### How the coach stays grounded
1. Before the model speaks, the server runs Stockfish on the current FEN and injects the result.
2. The model can call `evaluate_fen`, `test_candidate_move`, `get_legal_moves`, `get_tactical_motifs`; tool errors come back as data.
3. Board drawings are inline tags (`[[arrow:e2e4:green]]`, `[[hl:f7]]`); they are stripped from the text, validated, and
   dropped if malformed, so the model can never show raw markup or point at a non-square.
4. If the LLM call fails, the request falls back to the offline coach.

## Deploy to Vercel (hosted mode)

Vercel has no warm processes, disk or GPU, so the hosted build is a **stateless** variant: it spawns a couple of
Stockfish processes per request, keeps games in the browser tab (no database), and uses the offline coach. It switches on
automatically when the `VERCEL` env var is present (or set `CHESSY_HOSTED=1`).

```bash
./scripts/fetch_stockfish_linux.sh     # downloads the Linux x86-64 Stockfish into vendor/ (98 MB, gitignored)
npx vercel deploy --prod               # uploads the app + binary from disk
```
Hosted limits (env-tunable): `HOSTED_MAX_DEPTH=14`, `HOSTED_MAX_PLIES=200`, `HOSTED_POOL_SIZE=2`.
Rate limits per visitor IP (HTTP 429 with a clear message and `Retry-After`; `0` disables a limit):
`HOSTED_RATE_ANALYZE=10`, `HOSTED_RATE_EVALUATE=120`, `HOSTED_RATE_COACH=30` requests per `HOSTED_RATE_WINDOW=3600` seconds.
Visitors who send a valid access code are exempt. By default the counters live in memory, so on a serverless platform
they are per instance and best-effort: a visitor spread across instances (or a cold start) can get a few extra
requests. For shared counts, create an [Upstash Redis](https://upstash.com) database (the Vercel integration sets these
for you) and set `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN`; every instance then counts in one place. The
shared counter is a fixed window (one atomic `INCR` per request), so a visitor can briefly get up to twice the limit
across a window boundary, and only a hash of the visitor's address is stored. If Upstash can't be reached the request
is still served and limited in memory until it is back (one warning in the logs). The Upstash path is unit-tested
against a mocked REST API but has **not been run against a live database**. The client address is the last `X-Forwarded-For` entry, which Vercel sets; behind a proxy that doesn't, every visitor
would share one bucket. Saved games, insights
and the local AI coach are local-only. To restrict who can use a public deployment, set `CHESSY_ACCESS_CODE` in the
Vercel project's environment variables; the UI then asks visitors for the code.
Measured on Vercel: ~2 vCPU Xeon with AVX2, a 42-move game at depth 14 takes ~22 s, cold start ~5 s.

## Layout
```
app/main.py            FastAPI app + lifespan (engine pool, DB, cache, coach)
app/engine/            async Stockfish pool, cache, win-prob, classifier, motifs, visuals, review
app/coach/             agent loop, tools, prompts, provider adapters, offline coach, UI-action parser
app/services/          PGN trees, Chess.com/Lichess importers, masters lookup, player analytics
app/models/            Pydantic schemas + SQLAlchemy async models (SQLite)
app/data/example.json  precomputed depth-18 review of the example game (generated, committed)
tools/                 maintenance scripts (build_example.py)
web/                   the UI (plain HTML/CSS/JS, served at /)
tests/                 pytest suite (77 tests; engine tests skip if Stockfish is missing)
```

## The example game
"Try an example game" doesn't run the engine: it loads a precomputed depth-18 review from `app/data/example.json` and replays it move by move.
Analysis is deterministic (every search starts from a clean engine), so regenerating gives the same file on any machine with the same Stockfish version:

```bash
.venv/bin/python tools/build_example.py      # ~1 min; writes app/data/example.json
git add app/data/example.json
```
Regenerate after changing anything in the analysis output (move payload, summary, classifier, Stockfish version).
`tests/test_example.py` fails with "example.json is stale" when the saved file no longer matches the live response format.

## Notes and limits
- Analysis covers the **mainline**; variations are stored and returned in the tree, and any line can be explored on demand.
- Evaluations are cached by `(position, depth, multipv)` in memory; set `REDIS_URL` to use Redis (untested here).
- The Anthropic/OpenAI coach paths are unit-tested with scripted adapters but have **not been run against the live APIs**. The local path was run against a real `qwen2.5:3b`.
- Local-model answers can still be shallow or slightly off-topic (a 3B model); the move-level facts are engine-verified, the phrasing is not. Try `./setup_local_llm.sh qwen2.5:7b` for better answers.
- Click-to-move auto-queens on promotion.
