# ♞ Chessy

Free, open-source chess game review: streaming Stockfish analysis, a what-if explorer, and a coach that
**checks the engine before it speaks**. FastAPI backend, zero-dependency browser UI.

## Quick start

```bash
./setup.sh --test          # venv + deps + Stockfish check/install + .env + test suite
.venv/bin/uvicorn app.main:app --port 8765 --reload
# open http://localhost:8765
```

Manual route: Python 3.11+, `pip install -r requirements.txt`, `brew install stockfish` (or `sudo apt install stockfish`),
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

## Layout
```
app/main.py            FastAPI app + lifespan (engine pool, DB, cache, coach)
app/engine/            async Stockfish pool, cache, win-prob, classifier, motifs, visuals, review
app/coach/             agent loop, tools, prompts, provider adapters, offline coach, UI-action parser
app/services/          PGN trees, Chess.com/Lichess importers, masters lookup, player analytics
app/models/            Pydantic schemas + SQLAlchemy async models (SQLite)
web/                   the UI (plain HTML/CSS/JS, served at /)
tests/                 pytest suite (67 tests; engine tests skip if Stockfish is missing)
```

## Notes and limits
- Analysis covers the **mainline**; variations are stored and returned in the tree, and any line can be explored on demand.
- Evaluations are cached by `(position, depth, multipv)` in memory; set `REDIS_URL` to use Redis (untested here).
- The Anthropic/OpenAI coach paths are unit-tested with scripted adapters but have **not been run against the live APIs**. The local path was run against a real `qwen2.5:3b`.
- Local-model answers can still be shallow or slightly off-topic (a 3B model); the move-level facts are engine-verified, the phrasing is not. Try `./setup_local_llm.sh qwen2.5:7b` for better answers.
- Click-to-move auto-queens on promotion.
