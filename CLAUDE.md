# CLAUDE.md — Chess Analyzer

## What This Is

A personal chess analysis PWA that:
- Fetches game history from chess.com (public API, no auth needed)
- Analyzes each game with Stockfish (centipawn evaluation)
- Generates plain-English explanations of mistakes using Claude API
- Surfaces cross-game patterns in a coaching report
- Runs as a Flask server accessible on laptop and iPhone (PWA)

## Running

```bash
# 1. Install deps (one-time)
pip3 install -r requirements.txt
brew install stockfish  # if not already installed

# 2. Configure (one-time)
cp config.example.json config.json
# Edit config.json: set chesscom_username, anthropic_api_key, stockfish_path

# 3. Run
python3 app.py
# Open http://localhost:5050
# On iPhone: Add to Home Screen for PWA
```

## Architecture

**Three-layer pipeline:**

- **`sources/chesscom.py`** — Fetches game history from `api.chess.com`. Caches to `.chesscom_cache.json` (1-hour TTL).
- **`analysis/engine.py`** — Stockfish wrapper. Iterates positions with `python-chess`, evaluates each, classifies moves as best/inaccuracy/mistake/blunder using centipawn thresholds (200/500/900cp). Only classifies the player's own moves.
- **`analysis/claude_explain.py`** — Calls `claude-sonnet-4-6` for plain-English explanations. Only called for mistakes/blunders. 150 token limit.
- **`analysis/patterns.py`** — Aggregates blunder stats across all analyzed games; calls Claude for a coaching report.
- **`app.py`** — Flask orchestrator. Routes, SQLite init, background analysis threads.

**Storage:** SQLite (`chess.db`) — tables: `games`, `analysis`, `patterns`.

**Analysis is async:** When a game is requested via `/api/game/<id>`, if not analyzed, a background thread runs Stockfish + Claude. The frontend polls `/api/game/<id>/status` every 4 seconds.

## Config Fields

| Field | Description |
|---|---|
| `chesscom_username` | Your chess.com username |
| `anthropic_api_key` | Anthropic API key |
| `stockfish_path` | Path to Stockfish binary (default: `/opt/homebrew/bin/stockfish`) |
| `stockfish_depth` | Analysis depth (default: 15) |
| `port` | Server port (default: 5050) |
| `fetch_months` | How many months of history to fetch (default: 3) |

## Thresholds

| Classification | Centipawn loss |
|---|---|
| Inaccuracy | > 200 cp |
| Mistake | > 500 cp |
| Blunder | > 900 cp |

## Key Files

- `app.py` — Flask app, all routes, background analysis queue
- `sources/chesscom.py` — chess.com API client
- `analysis/engine.py` — Stockfish integration
- `analysis/claude_explain.py` — Claude API for move explanations
- `analysis/patterns.py` — Cross-game pattern report
- `templates/index.html` — SPA shell (chessboard.js + chess.js from CDN)
- `static/js/app.js` — All UI logic
- `static/css/style.css` — Dark theme styles

## Notes for Claude Code

- Ian pre-authorized killing and relaunching the dev server whenever code changes need to load.
- Analysis is expensive (Stockfish + Claude per game). Results are cached in SQLite permanently.
- The app only analyzes the player's own moves, not the opponent's.
- Ian mainly plays rapid games (10+ min time control).
