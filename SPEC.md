# Chess Review — Project Spec

A personal tool for reviewing my own chess games with annotated, click-through replay.
Owner: Ian Christoph (Chess.com username: `ichristoph`)
Created: April 2026

---

## Why This Exists

I'm an ~850-rated chess.com player (rapid, 10-minute games, mostly on iPhone) working to improve. Chess.com's Game Review and Lichess analysis are good for engine evaluations but weak at *strategic explanation* and *pattern recognition across games*. I bring games to Claude for that kind of analysis. This tool is the front-end: a personal viewer where I can step through games while reading Claude's notes beside each move, and a back-end automation that pulls new games from Chess.com so I'm not copy-pasting PGN by hand.

This is also a learning project. I'm new to coding (Python beginner, comfortable with terminal/CLI basics, building a YNAB dashboard alongside this). Code should be readable and over-commented, not clever.

---

## Architecture (Phased)

### Phase 1 — Local Viewer (start here)

Static HTML file that reads PGN + markdown analysis pairs from local folders and renders an interactive board with annotations.

**Stack:**
- Single `index.html`, vanilla JavaScript, no build step
- Self-contained chess engine (no external CDN dependencies — the previous Claude artifact had CDN reliability issues with chess.js and chessboardjs in iOS webviews)
- CSS grid board with Unicode piece glyphs OR inline SVG pieces
- Loads PGN files via `fetch()` from the local `games/` folder
- Loads paired markdown analysis from `analyses/` folder

**Acceptance criteria for Phase 1:**
- Opens in Safari (macOS and iOS) and renders correctly
- Dropdown lists all PGN files in `games/`
- Selecting a game loads board + moves + paired analysis
- Click any move (or use ←/→ keys) to jump to that position
- Analysis pane shows the markdown for the current move (or general game notes when no move is selected)
- Flip-board button works (I play Black often)
- Last-move squares highlighted

### Phase 2 — Chess.com API Sync

Python script that pulls my recent games from the Chess.com Published Data API and drops new PGN files into `games/`.

**Endpoints:**
- `GET https://api.chess.com/pub/player/ichristoph/games/archives` — list of monthly archive URLs I have games in
- `GET https://api.chess.com/pub/player/ichristoph/games/{YYYY}/{MM}` — all games from that month, JSON with PGN embedded

**Behavior:**
- Default: fetch the current month's games
- Optional flag: `--since YYYY-MM-DD` to backfill
- Skip games already present in `games/` (dedupe by Chess.com `uuid` field, stored as a comment in the PGN or a sidecar file)
- Filename convention: `YYYY-MM-DD_opponent-username.pgn` (e.g. `2026-04-27_xcharged_creeperx.pgn`)
- Create an empty `analyses/{same-stem}.md` template for each new game with pre-filled headers (date, opponent, result, my color, time control, my rating, opponent rating, Chess.com accuracy if present)

**Notes on the API:**
- No auth required, no API key
- Rate limits are generous for personal use; still, sleep ~1s between archive requests to be polite
- Public games only — friend-only games won't appear (not a concern for me; standard pools are public by default)
- The `accuracies` field gives overall White/Black accuracy percentages from Game Review, but move-by-move engine evaluations are NOT exposed via API. If I want those, Phase 3.

### Phase 3 — Local Stockfish Annotation (later, optional)

Use the `python-chess` library + a local Stockfish binary to pre-annotate PGNs with eval shifts before I bring the game to Claude. The idea is to flag obvious blunders/mistakes/best-moves automatically so Claude (and I) can focus on the strategic explanation rather than tactical bookkeeping.

**Trigger to build this:** if I'm consistently asking Claude to identify the critical moments first, automating that becomes worth the effort. Don't build until then.

### Phase 4 — Possible Extensions (don't pre-build)

- Pattern tracker across games (e.g., "missed opponent threat on move N" tagged on multiple games — surface recurring themes)
- Side-by-side comparison of two games
- Export annotated game to PDF for printing
- Integration with my YNAB dashboard's Claude Code patterns (shared utility modules?)

---

## File Layout

```
chess_review/
  index.html                  ← Phase 1 viewer
  fetch_games.py              ← Phase 2 sync script
  README.md                   ← how to run things
  SPEC.md                     ← this file
  requirements.txt            ← Python deps for fetch_games.py
  .gitignore                  ← if I version-control this
  games/                      ← .pgn files, one game each
    2026-04-25_aswath.pgn
    2026-04-25_burberry.pgn
    2026-04-26_violetaristi.pgn
    2026-04-27_xcharged_creeperx.pgn
    ...
  analyses/                   ← markdown notes paired by filename
    2026-04-25_aswath.md
    ...
  .sync_state.json            ← tracks fetched game UUIDs (Phase 2)
```

The viewer's "load a game" flow: read directory listing of `games/`, populate dropdown, on selection fetch the PGN and the matching `.md` from `analyses/`.

**Important constraint:** Browsers don't normally allow `file://` JS to read directory listings. Two ways to handle this:

1. **Manifest file approach (preferred for simplicity):** maintain a `games/index.json` that lists all PGN filenames. The Python sync script regenerates this on every run. The HTML reads that manifest.
2. **Local server approach:** run `python -m http.server` from the project folder. Slightly more setup; gives a real directory listing. Worth it if Phase 4 grows the tool.

Start with the manifest approach. It's simpler and works offline without any process running.

---

## Analysis Markdown Format

Each `analyses/{game-id}.md` follows this structure so the viewer can extract per-move notes:

```markdown
# {date} — vs {opponent} ({result})

**My color:** Black
**Time control:** 10+0 rapid
**Ratings:** me 823, opponent 861
**Chess.com accuracy:** me 76%, opponent 82%

## Summary

Two-paragraph overview. Big picture lesson, what worked, what failed.

## Move-by-move

### 6...e5
The losing pawn — when I traded knights on c6, the pawn that defended e5 disappeared. Always re-check what a piece was defending before you trade it off.

### 16.Bb2
Opponent's threat I missed. The bishop attacked my rook on c3, defended only by the knight. I should have moved the rook before doing anything else.

### 41.Kh7
The blunder that cost the exchange. (Same root cause as move 16 — failure to ask "what does my opponent's last move threaten?")

## Lessons to remember

- When trading a piece, recheck what it was defending
- Before every move: "what does my opponent's last move threaten?"
- When winning, slow down — that's exactly when blunders happen
```

The viewer parses `### {move-notation}` headers to associate notes with positions. Anything before the first `### ` is "general notes" shown when no move is selected. Lessons section displays at the end of the game (move N+1 view).

---

## Coding Style Preferences

- **Vanilla JS, no React/Vue/build tools for Phase 1.** Single HTML file should open and work.
- **No external CDN dependencies in the viewer.** Self-contained — works offline, works in any webview, no future bit-rot from dead URLs.
- **Python 3.11+ for scripts.** Use only standard library + `requests` if needed (or `urllib` to avoid even that).
- **Comments aimed at me as a beginner.** Explain *why*, not just *what*.
- **No premature abstraction.** First version should be flat and readable. Refactor when there's a second use case, not before.

---

## Workflow Once Built

1. Play games on Chess.com iPhone app as usual.
2. Once a week (or after an interesting session): `python fetch_games.py` — pulls new games into `games/`, creates blank analysis files in `analyses/`.
3. Open `index.html` in Safari. Pick a game from the dropdown.
4. Bring the game to Claude (paste the PGN). Claude writes the analysis markdown. I save it as `analyses/{game-id}.md`, replacing the blank template.
5. Reload the viewer. Step through the game with Claude's notes alongside. Print the lessons section if it's a particularly instructive game.

---

## Open Questions to Resolve When Building

- Should the viewer support multiple analyses per game? (e.g., a first-pass analysis and a deeper review later.) → Probably YES eventually, but Phase 1 is one-analysis-per-game.
- How to handle games where I want to add my own notes ("what I was thinking on this move")? → Phase 1: just edit the markdown manually. Phase 4: in-app editor.
- Should the dropdown sort newest-first or have search/filter? → Newest-first by date in filename. Filter when I have >50 games.
- iCloud sync: the project lives in `~/Documents/Ians Programs/` which is iCloud-synced. Verify the viewer works correctly when accessed from iPhone Safari via the Files app. → Test this in Phase 1 acceptance.

---

## Resources

- Chess.com Published Data API docs: https://www.chess.com/news/view/published-data-api
- python-chess library: https://python-chess.readthedocs.io/
- Stockfish download: https://stockfishchess.org/download/
- PGN format reference: https://en.wikipedia.org/wiki/Portable_Game_Notation
- Earlier Claude artifact (proof-of-concept viewer with self-contained engine): saved as `pgn_viewer_v1.html` in this folder for reference; the inline chess engine and SAN parser there can be reused/improved.

---

## First Session Prompt for Claude Code

When ready to start Phase 1, open Claude Code in `~/Documents/Ians Programs/chess_review/` and paste:

> Read SPEC.md. Build Phase 1 — the local viewer (`index.html`). Use the inline chess engine approach from the earlier proof-of-concept, not chess.js/chessboardjs (CDN reliability issues in iOS webviews). Self-contained single file. Pull a couple of my recent games from `games/` (I'll have already dropped some PGN files in there) and the matching analyses from `analyses/`. Confirm acceptance criteria from SPEC.md before declaring done. Keep code beginner-readable; comment generously.
