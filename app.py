"""Flask app — Chess Analyzer server."""

import json
import os
import sqlite3
import threading
import time

from flask import Flask, jsonify, render_template, request, send_from_directory

from analysis.claude_explain import explain_game
from analysis.engine import annotate_game, get_best_move
from analysis.patterns import generate_patterns
from sources.chesscom import fetch_games

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')

def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(
            "config.json not found. Copy config.example.json → config.json and fill in your values."
        )
    with open(CONFIG_PATH) as f:
        return json.load(f)

CONFIG = load_config()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), 'chess.db')

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS games (
            id         TEXT PRIMARY KEY,
            url        TEXT,
            pgn        TEXT,
            username   TEXT,
            played_as  TEXT,
            opponent   TEXT,
            result     TEXT,
            time_class TEXT,
            end_time   INTEGER,
            analyzed   INTEGER DEFAULT 0,
            eco        TEXT,
            opening    TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS analysis (
            game_id    TEXT PRIMARY KEY,
            moves_json TEXT,
            claude_ok  INTEGER DEFAULT 1
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS patterns (
            username     TEXT PRIMARY KEY,
            report       TEXT,
            generated_at INTEGER
        )
    """)
    # Safe migrations for existing databases
    for alter in [
        "ALTER TABLE games ADD COLUMN eco TEXT",
        "ALTER TABLE games ADD COLUMN opening TEXT",
        "ALTER TABLE analysis ADD COLUMN claude_ok INTEGER DEFAULT 1",
    ]:
        try:
            c.execute(alter)
        except Exception:
            pass  # column already exists
    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ---------------------------------------------------------------------------
# Routes — Pages
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html', username=CONFIG.get('chesscom_username', ''))


@app.route('/manifest.json')
def manifest():
    return send_from_directory(os.path.dirname(__file__), 'manifest.json')

# ---------------------------------------------------------------------------
# Routes — Games API
# ---------------------------------------------------------------------------

@app.route('/api/games')
def list_games():
    conn = get_db()
    rows = conn.execute(
        """SELECT id, url, username, played_as, opponent, result, time_class, end_time, analyzed, eco, opening
           FROM games ORDER BY end_time DESC LIMIT 100"""
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/games/fetch', methods=['POST'])
def fetch():
    username = CONFIG.get('chesscom_username', '').strip()
    if not username:
        return jsonify({'error': 'chesscom_username not set in config.json'}), 400

    months = CONFIG.get('fetch_months', 3)
    try:
        games = fetch_games(username, months)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    conn = get_db()
    added = 0
    for g in games:
        conn.execute(
            """INSERT OR IGNORE INTO games
               (id, url, pgn, username, played_as, opponent, result, time_class, end_time, eco, opening)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (g['id'], g['url'], g['pgn'], username,
             g['played_as'], g['opponent'], g['result'],
             g['time_class'], g['end_time'],
             g.get('eco'), g.get('opening')),
        )
        if conn.execute('SELECT changes()').fetchone()[0]:
            added += 1
    conn.commit()
    conn.close()
    return jsonify({'added': added, 'total': len(games)})


@app.route('/api/game/<game_id>')
def get_game(game_id):
    conn = get_db()
    game = conn.execute('SELECT * FROM games WHERE id = ?', (game_id,)).fetchone()
    if not game:
        conn.close()
        return jsonify({'error': 'Game not found'}), 404

    analysis = conn.execute(
        'SELECT moves_json, claude_ok FROM analysis WHERE game_id = ?', (game_id,)
    ).fetchone()
    conn.close()

    result = dict(game)
    if analysis and analysis['moves_json']:
        claude_ok = bool(analysis['claude_ok']) if analysis['claude_ok'] is not None else True
        moves = json.loads(analysis['moves_json'])
        result['moves']    = moves
        result['analyzing'] = False
        result['claude_ok'] = claude_ok

        if not claude_ok:
            # Commentary incomplete — either still running or needs resuming
            if game_id not in _analysis_in_progress:
                # Thread died (server restart, crash, timeout) — resume commentary only
                _maybe_start_commentary(game_id, dict(game), moves)
            result['commentary_pending'] = True
        else:
            result['commentary_pending'] = False
    else:
        # No analysis at all — kick off full Stockfish + Claude pipeline
        _maybe_start_analysis(game_id, dict(game))
        result['moves']              = None
        result['analyzing']          = True
        result['commentary_pending'] = False

    return jsonify(result)


@app.route('/api/game/<game_id>/analyze', methods=['POST'])
def trigger_analysis(game_id):
    conn = get_db()
    game = conn.execute('SELECT * FROM games WHERE id = ?', (game_id,)).fetchone()
    if not game:
        conn.close()
        return jsonify({'error': 'Game not found'}), 404
    # Clear old analysis so the full two-phase pipeline reruns cleanly
    conn.execute('DELETE FROM analysis WHERE game_id = ?', (game_id,))
    conn.execute('UPDATE games SET analyzed = 0 WHERE id = ?', (game_id,))
    conn.commit()
    conn.close()
    _maybe_start_analysis(game_id, dict(game), force=True)
    return jsonify({'status': 'analyzing'})


@app.route('/api/game/<game_id>/status')
def analysis_status(game_id):
    conn = get_db()
    row = conn.execute(
        'SELECT analyzed FROM games WHERE id = ?', (game_id,)
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'not found'}), 404
    return jsonify({'analyzed': bool(row['analyzed'])})

# ---------------------------------------------------------------------------
# Routes — Patterns API
# ---------------------------------------------------------------------------

@app.route('/api/patterns')
def patterns():
    username = CONFIG.get('chesscom_username', '')
    conn = get_db()
    row = conn.execute(
        'SELECT report, generated_at FROM patterns WHERE username = ?', (username,)
    ).fetchone()
    conn.close()
    if row:
        return jsonify({'report': row['report'], 'generated_at': row['generated_at']})
    return jsonify({'report': None, 'generated_at': None})


@app.route('/api/chat', methods=['POST'])
def chat():
    body = request.get_json(force=True)
    game_id  = body.get('game_id')
    move_idx = body.get('move_idx')
    message  = (body.get('message') or '').strip()
    history  = body.get('history') or []

    if not message:
        return jsonify({'error': 'empty message'}), 400

    api_key = CONFIG.get('anthropic_api_key')
    if not api_key:
        return jsonify({'error': 'no API key configured'}), 400

    # Load context for this move
    context = _build_chat_context(game_id, move_idx)

    # Run Stockfish live for the current FEN so move suggestions are accurate.
    stockfish_note = ''
    current_fen = _get_current_fen(game_id, move_idx)
    if current_fen:
        sf_move, sf_cp = get_best_move(current_fen, CONFIG)
        if sf_move:
            sign = '+' if (sf_cp or 0) >= 0 else ''
            cp_str = f' (eval: {sign}{(sf_cp or 0)/100:.1f} pawns for white)' if sf_cp is not None else ''
            stockfish_note = (
                f"\n\nStockfish best move for the current position: {sf_move}{cp_str}.\n"
                "IMPORTANT: If the user asks what move to play or for a suggestion, "
                f"you MUST recommend {sf_move}. Do not suggest any other move. "
                "Explain WHY Stockfish recommends this move in plain English."
            )

    import anthropic as _anthropic
    client = _anthropic.Anthropic(api_key=api_key)

    system = (
        "You are a friendly chess coach helping a beginner understand their game. "
        "Answer questions about the current position clearly and concisely in plain English. "
        "No long move sequences. "
        "CRITICAL: You cannot calculate chess moves reliably. Never suggest a move from your own reasoning. "
        "If the user asks what to play, always use the Stockfish recommendation provided in the context."
        + (f"\n\nCurrent position context:\n{context}" if context else "")
        + stockfish_note
    )

    messages = history + [{'role': 'user', 'content': message}]

    try:
        resp = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=400,
            system=system,
            messages=messages,
        )
        reply = resp.content[0].text.strip()
        return jsonify({'reply': reply})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _build_chat_context(game_id, move_idx):
    if not game_id or move_idx is None:
        return ''
    try:
        conn = get_db()
        game     = conn.execute('SELECT played_as, result, opening, eco FROM games WHERE id=?', (game_id,)).fetchone()
        analysis = conn.execute('SELECT moves_json FROM analysis WHERE game_id=?', (game_id,)).fetchone()
        conn.close()
        if not game or not analysis:
            return ''
        moves = json.loads(analysis['moves_json'])
        if move_idx < 0 or move_idx >= len(moves):
            return ''

        m         = moves[move_idx]
        played_as = game['played_as']

        lines = [
            f"Player is {played_as}, game result: {game['result']}.",
        ]
        if game['opening']:
            eco = f" ({game['eco']})" if game['eco'] else ''
            lines.append(f"Opening: {game['opening']}{eco}")

        lines.append(f"\nCurrent move: {m['move_number']}. {m['san']} (played by {m['color']})")
        lines.append(f"Position FEN: {m['fen']}")

        # Eval context
        e_before = m.get('eval_before')
        e_after  = m.get('eval_after')
        if e_before is not None and e_after is not None:
            sb = '+' if e_before >= 0 else ''
            sa = '+' if e_after  >= 0 else ''
            lines.append(f"Evaluation: {sb}{e_before/100:.1f} → {sa}{e_after/100:.1f} pawns (white's POV)")

        if m.get('classification') and m['classification'] != 'best':
            lines.append(f"Stockfish classified this as: {m['classification']}")
        if m.get('best_move') and m['best_move'] != m['san']:
            lines.append(f"Stockfish preferred: {m['best_move']}")
        if m.get('explanation'):
            lines.append(f"Original coaching note: {m['explanation']}")

        # 3 moves of context before and after
        ctx_before = moves[max(0, move_idx - 3):move_idx]
        ctx_after  = moves[move_idx + 1:move_idx + 4]
        if ctx_before:
            lines.append("\nPreceding moves:")
            for cm in ctx_before:
                dot = '.' if cm['color'] == 'white' else '...'
                lines.append(f"  {cm['move_number']}{dot} {cm['san']} [{cm.get('classification','?')}]")
        if ctx_after:
            lines.append("Following moves:")
            for cm in ctx_after:
                dot = '.' if cm['color'] == 'white' else '...'
                lines.append(f"  {cm['move_number']}{dot} {cm['san']} [{cm.get('classification','?')}]")

        return '\n'.join(lines)
    except Exception:
        return ''


def _get_current_fen(game_id, move_idx):
    """Return the FEN for the position at move_idx (before the move is played)."""
    if not game_id:
        return None
    try:
        conn = get_db()
        analysis = conn.execute('SELECT moves_json FROM analysis WHERE game_id=?', (game_id,)).fetchone()
        conn.close()
        if not analysis:
            return None
        moves = json.loads(analysis['moves_json'])
        if move_idx is None or move_idx < 0:
            return None
        if move_idx < len(moves):
            # fen_before is the position the player is deciding in — that's what we want
            return moves[move_idx].get('fen_before') or moves[move_idx].get('fen')
        # Past the end of recorded moves — use last fen
        return moves[-1]['fen'] if moves else None
    except Exception:
        return None


@app.route('/api/patterns/generate', methods=['POST'])
def gen_patterns():
    username = CONFIG.get('chesscom_username', '')
    if not username:
        return jsonify({'error': 'chesscom_username not set'}), 400

    def _run():
        report = generate_patterns(username, DB_PATH, CONFIG)
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            'INSERT OR REPLACE INTO patterns (username, report, generated_at) VALUES (?,?,?)',
            (username, report, int(time.time())),
        )
        conn.commit()
        conn.close()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'status': 'generating'})

# ---------------------------------------------------------------------------
# Background analysis
# ---------------------------------------------------------------------------

_analysis_in_progress = set()
_analysis_lock = threading.Lock()


def _maybe_start_analysis(game_id, game, force=False):
    with _analysis_lock:
        if game_id in _analysis_in_progress and not force:
            return
        _analysis_in_progress.add(game_id)
    threading.Thread(target=_run_analysis, args=(game_id, game), daemon=True).start()


def _maybe_start_commentary(game_id, game, moves):
    """Resume just the Claude commentary phase for a game that has Stockfish data."""
    with _analysis_lock:
        if game_id in _analysis_in_progress:
            return
        _analysis_in_progress.add(game_id)
    threading.Thread(target=_run_commentary, args=(game_id, game, moves), daemon=True).start()


def _run_commentary(game_id, game, moves):
    """Run only the Claude explanation phase, then update the DB."""
    try:
        played_as = game['played_as']
        moves = explain_game(moves, played_as, CONFIG, game_result=game.get('result', 'unknown'))
        claude_ok = any(m.get('explanation') for m in moves)

        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            'UPDATE analysis SET moves_json=?, claude_ok=? WHERE game_id=?',
            (json.dumps(moves), int(claude_ok), game_id),
        )
        conn.commit()
        conn.close()
        print(f"Commentary complete for {game_id}: claude_ok={claude_ok}")
    except Exception as e:
        print(f"Commentary failed for {game_id}: {e}")
    finally:
        with _analysis_lock:
            _analysis_in_progress.discard(game_id)


def _run_analysis(game_id, game):
    try:
        played_as = game['played_as']
        pgn       = game['pgn']

        # Phase 1: Stockfish annotation — fast (~5s). Save immediately so the
        # board is viewable while Claude runs in the background.
        moves = annotate_game(pgn, played_as, CONFIG)

        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            'INSERT OR REPLACE INTO analysis (game_id, moves_json, claude_ok) VALUES (?,?,?)',
            (game_id, json.dumps(moves), 0),  # claude_ok=0 → commentary pending
        )
        conn.execute('UPDATE games SET analyzed = 1 WHERE id = ?', (game_id,))
        conn.commit()
        conn.close()

        # Phase 2: Claude commentary — slow (~60-120s). Update in place.
        moves     = explain_game(moves, played_as, CONFIG, game_result=game.get('result', 'unknown'))
        claude_ok = any(m.get('explanation') for m in moves)

        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            'UPDATE analysis SET moves_json=?, claude_ok=? WHERE game_id=?',
            (json.dumps(moves), int(claude_ok), game_id),
        )

        # Auto-regenerate patterns every 5 newly analyzed games
        username = game.get('username', '')
        if username:
            count = conn.execute(
                'SELECT COUNT(*) FROM games WHERE analyzed=1 AND username=?', (username,)
            ).fetchone()[0]
            if count > 0 and count % 5 == 0:
                threading.Thread(
                    target=_auto_gen_patterns, args=(username,), daemon=True
                ).start()

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Analysis failed for {game_id}: {e}")
    finally:
        with _analysis_lock:
            _analysis_in_progress.discard(game_id)


def _auto_gen_patterns(username):
    """Background pattern regeneration triggered automatically every 5 analyzed games."""
    try:
        report = generate_patterns(username, DB_PATH, CONFIG)
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            'INSERT OR REPLACE INTO patterns (username, report, generated_at) VALUES (?,?,?)',
            (username, report, int(time.time())),
        )
        conn.commit()
        conn.close()
        print(f"Auto-regenerated patterns for {username}")
    except Exception as e:
        print(f"Auto pattern generation failed: {e}")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    init_db()
    port = CONFIG.get('port', 5050)
    print(f"Chess Analyzer → http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
