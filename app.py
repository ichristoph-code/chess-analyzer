"""Flask app — Chess Analyzer server."""

import json
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, render_template, request, send_from_directory

from analysis.blueprint import generate_blueprint
from analysis.claude_explain import (
    commentary_incomplete,
    explain_game,
    fill_fast_comments,
    finalize_fast_comments,
    generate_game_summary,
    needs_claude_commentary,
)
from analysis.engine import annotate_game, get_best_move
from analysis.patterns import generate_patterns, get_player_weakness_summary
from sources.chesscom import fetch_games

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
MODEL_PRESETS = {
    'sonnet': 'claude-sonnet-4-6',
    'opus': 'claude-opus-4-7',
}

def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(
            "config.json not found. Copy config.example.json → config.json and fill in your values."
        )
    with open(CONFIG_PATH) as f:
        return json.load(f)

CONFIG = load_config()

def save_config():
    tmp_path = CONFIG_PATH + '.tmp'
    with open(tmp_path, 'w') as f:
        json.dump(CONFIG, f, indent=2)
        f.write('\n')
    os.replace(tmp_path, CONFIG_PATH)

def current_model_mode():
    explain_model = CONFIG.get('explain_model')
    for mode, model in MODEL_PRESETS.items():
        if explain_model == model:
            return mode
    return 'custom'

def friendly_api_error(error):
    msg = str(error)
    if 'credit balance is too low' in msg:
        return (
            "Anthropic says this API key's credit balance is too low. "
            "Check the Anthropic Console organization/workspace tied to this key, "
            "not just your Claude subscription or another billing workspace."
        )
    return msg

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), 'chess.db')


@app.after_request
def _no_cache_for_api(response):
    """Never cache API responses — prevents stale commentary in the browser after refresh."""
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
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
        "ALTER TABLE analysis ADD COLUMN game_summary TEXT",
    ]:
        try:
            c.execute(alter)
        except Exception:
            pass  # column already exists
    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # safe for concurrent readers + writer
    return conn

# ---------------------------------------------------------------------------
# Routes — Pages
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    js_path = os.path.join(os.path.dirname(__file__), 'static', 'js', 'app.js')
    try:
        mtime = str(int(os.path.getmtime(js_path)))
    except OSError:
        mtime = '1'
    return render_template('index.html', username=CONFIG.get('chesscom_username', ''),
                           static_version=mtime)


@app.route('/manifest.json')
def manifest():
    return send_from_directory(os.path.dirname(__file__), 'manifest.json')


@app.route('/sw.js')
def service_worker():
    return send_from_directory(os.path.dirname(__file__), 'sw.js', mimetype='application/javascript')

# ---------------------------------------------------------------------------
# Routes — Games API
# ---------------------------------------------------------------------------

@app.route('/api/games')
def list_games():
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT id, url, username, played_as, opponent, result, time_class, end_time, analyzed, eco, opening
               FROM games ORDER BY end_time DESC LIMIT 100"""
        ).fetchall()
    finally:
        conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/model', methods=['GET', 'POST'])
def model_settings():
    if request.method == 'GET':
        return jsonify({
            'mode': current_model_mode(),
            'explain_model': CONFIG.get('explain_model'),
            'chat_model': CONFIG.get('chat_model'),
            'patterns_model': CONFIG.get('patterns_model'),
        })

    body = request.get_json(silent=True) or {}
    mode = body.get('mode')
    if mode not in MODEL_PRESETS:
        return jsonify({'error': 'Choose sonnet or opus'}), 400

    model = MODEL_PRESETS[mode]
    CONFIG['explain_model'] = model
    CONFIG['chat_model'] = model
    CONFIG['patterns_model'] = model
    save_config()

    return jsonify({
        'mode': mode,
        'explain_model': model,
        'chat_model': model,
        'patterns_model': model,
    })


@app.route('/api/games/fetch', methods=['POST'])
def fetch():
    username = CONFIG.get('chesscom_username', '').strip()
    if not username:
        return jsonify({'error': 'chesscom_username not set in config.json'}), 400

    months = CONFIG.get('fetch_months', 3)
    try:
        games = fetch_games(username, months, force=True)
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
    try:
        game     = conn.execute('SELECT * FROM games WHERE id = ?', (game_id,)).fetchone()
        analysis = conn.execute(
            'SELECT moves_json, claude_ok, game_summary FROM analysis WHERE game_id = ?', (game_id,)
        ).fetchone() if game else None
    finally:
        conn.close()

    if not game:
        return jsonify({'error': 'Game not found'}), 404

    result = dict(game)
    if analysis and analysis['moves_json']:
        try:
            moves = json.loads(analysis['moves_json'])
        except (json.JSONDecodeError, TypeError):
            moves = None

        if moves is not None:
            claude_ok = bool(analysis['claude_ok']) if analysis['claude_ok'] is not None else True
            result['moves']        = moves
            result['analyzing']    = False
            result['claude_ok']    = claude_ok
            result['game_summary'] = analysis['game_summary'] or None

            if not claude_ok:
                moves = fill_fast_comments(moves, result.get('played_as'))
                result['moves'] = moves
                # Only auto-retry commentary a few times — without this cap, a
                # broken API key would trigger a fresh paid Claude call on every
                # 5-second status poll, forever.
                with _analysis_lock:
                    pending = game_id in _analysis_in_progress
                    can_retry = _commentary_failures.get(game_id, 0) < MAX_COMMENTARY_RETRIES
                if not pending and can_retry:
                    _maybe_start_commentary(game_id, dict(game), moves)
                    pending = True
                result['commentary_pending'] = pending
            else:
                moves = finalize_fast_comments(moves, result.get('played_as'))
                result['moves'] = moves
                result['commentary_pending'] = False
            return jsonify(result)

    # No analysis (or corrupted) — kick off full Stockfish + Claude pipeline
    _maybe_start_analysis(game_id, dict(game))
    result['moves']              = None
    result['analyzing']          = True
    result['commentary_pending'] = False
    return jsonify(result)


@app.route('/api/game/<game_id>/analyze', methods=['POST'])
def trigger_analysis(game_id):
    conn = get_db()
    try:
        game = conn.execute('SELECT * FROM games WHERE id = ?', (game_id,)).fetchone()
        if not game:
            return jsonify({'error': 'Game not found'}), 404
        conn.execute('DELETE FROM analysis WHERE game_id = ?', (game_id,))
        conn.execute('UPDATE games SET analyzed = 0 WHERE id = ?', (game_id,))
        conn.commit()
    finally:
        conn.close()
    _maybe_start_analysis(game_id, dict(game), force=True)
    return jsonify({'status': 'analyzing'})


@app.route('/api/game/<game_id>/redo-commentary', methods=['POST'])
def redo_commentary(game_id):
    """Re-run only the Claude commentary step, keeping Stockfish data intact."""
    conn = get_db()
    try:
        game = conn.execute('SELECT * FROM games WHERE id = ?', (game_id,)).fetchone()
        if not game:
            return jsonify({'error': 'Game not found'}), 404
        analysis = conn.execute(
            'SELECT moves_json FROM analysis WHERE game_id = ?', (game_id,)
        ).fetchone()
        if not analysis:
            return jsonify({'error': 'No Stockfish data yet — run full analysis first'}), 400
        moves = json.loads(analysis['moves_json'])
    finally:
        conn.close()

    with _analysis_lock:
        _commentary_failures.pop(game_id, None)   # explicit redo resets the retry budget
    _maybe_start_commentary(game_id, dict(game), moves)
    return jsonify({'status': 'commentary_queued'})


@app.route('/api/commentary/refresh-all', methods=['POST'])
def refresh_all_commentary():
    """Re-run Claude commentary for every analyzed game."""
    conn = get_db()
    try:
        username = CONFIG.get('chesscom_username', '')
        rows = conn.execute(
            'SELECT g.id, g.*, a.moves_json FROM games g JOIN analysis a ON g.id = a.game_id '
            'WHERE g.username = ? AND g.analyzed = 1 AND a.moves_json IS NOT NULL',
            (username,),
        ).fetchall()
    finally:
        conn.close()

    queued = 0
    for row in rows:
        moves = json.loads(row['moves_json'])
        _maybe_start_commentary(row['id'], dict(row), moves)
        queued += 1

    return jsonify({'status': 'queued', 'count': queued})


@app.route('/api/game/<game_id>/status')
def analysis_status(game_id):
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT analyzed FROM games WHERE id = ?', (game_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({'error': 'not found'}), 404
    with _analysis_lock:
        prog = _analysis_progress.get(game_id)
    return jsonify({
        'analyzed': bool(row['analyzed']),
        'progress': {'done': prog[0], 'total': prog[1]} if prog else None,
    })

# ---------------------------------------------------------------------------
# Routes — Patterns API
# ---------------------------------------------------------------------------

@app.route('/api/patterns')
def patterns():
    username = CONFIG.get('chesscom_username', '')
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT report, generated_at FROM patterns WHERE username = ?', (username,)
        ).fetchone()
    finally:
        conn.close()
    if row:
        return jsonify({'report': row['report'], 'generated_at': row['generated_at']})
    return jsonify({'report': None, 'generated_at': None})


@app.route('/api/blueprint')
def blueprint():
    username = CONFIG.get('chesscom_username', '')
    if not username:
        return jsonify({'error': 'chesscom_username not set'}), 400
    return jsonify(generate_blueprint(username, DB_PATH, CONFIG))


@app.route('/api/chat', methods=['POST'])
def chat():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({'error': 'invalid JSON'}), 400
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
        "If the user asks what to play, always use the Stockfish recommendation provided in the context.\n\n"
        "CRITICAL: The context below contains an 'Exact piece locations' section that was machine-parsed "
        "directly from the FEN by python-chess — it is 100% accurate. You MUST use it as ground truth "
        "for all piece and pawn locations. Do NOT attempt to re-parse the FEN yourself. "
        "If a piece or pawn is not listed there, it does not exist on the board. "
        "Never assert a piece is on a square unless it appears in that list."
        + (f"\n\nCurrent position context:\n{context}" if context else "")
        + stockfish_note
    )

    messages = history + [{'role': 'user', 'content': message}]

    try:
        resp = client.messages.create(
            model=CONFIG.get('chat_model', CONFIG.get('explain_model', MODEL_PRESETS['sonnet'])),
            max_tokens=400,
            system=system,
            messages=messages,
        )
        reply = resp.content[0].text.strip()
        return jsonify({'reply': reply})
    except Exception as e:
        return jsonify({'error': friendly_api_error(e)}), 500


def _build_chat_context(game_id, move_idx):
    if not game_id or move_idx is None:
        return ''
    try:
        conn = get_db()
        try:
            game     = conn.execute('SELECT played_as, result, opening, eco FROM games WHERE id=?', (game_id,)).fetchone()
            analysis = conn.execute('SELECT moves_json FROM analysis WHERE game_id=?', (game_id,)).fetchone()
        finally:
            conn.close()
        if not game or not analysis:
            return ''
        try:
            moves = json.loads(analysis['moves_json'])
        except (json.JSONDecodeError, TypeError):
            return ''
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

        # Add machine-parsed board state so Claude doesn't have to parse FEN mentally
        fen_for_board = m.get('fen_before') or m.get('fen')
        board_lines = _fen_board_state(fen_for_board)
        if board_lines:
            lines.append("Exact piece locations after this move (machine-parsed, 100% accurate — do NOT contradict this):")
            lines.extend(board_lines)

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


def _fen_board_state(fen: str) -> list:
    """Return plain-English lines listing every piece by square, parsed by python-chess.

    This is authoritative — Claude must not contradict it by trying to re-parse the FEN.
    """
    if not fen:
        return []
    try:
        import chess as _chess
        board = _chess.Board(fen)
        _NAMES = {
            _chess.KING: 'King', _chess.QUEEN: 'Queen', _chess.ROOK: 'Rook',
            _chess.BISHOP: 'Bishop', _chess.KNIGHT: 'Knight', _chess.PAWN: 'Pawn',
        }
        order = [_chess.KING, _chess.QUEEN, _chess.ROOK, _chess.BISHOP, _chess.KNIGHT, _chess.PAWN]
        lines = []
        for color, label in ((_chess.WHITE, 'White'), (_chess.BLACK, 'Black')):
            parts = []
            for pt in order:
                sqs = sorted(board.pieces(pt, color), key=lambda s: (_chess.square_file(s), _chess.square_rank(s)))
                if sqs:
                    name = _NAMES[pt] + ('s' if len(sqs) > 1 else '')
                    parts.append(f"{name} {' '.join(_chess.square_name(s) for s in sqs)}")
            if parts:
                lines.append(f"  {label}: {', '.join(parts)}")
        return lines
    except Exception:
        return []


def _get_current_fen(game_id, move_idx):
    """Return the FEN for the position at move_idx (before the move is played)."""
    if not game_id or move_idx is None or move_idx < 0:
        return None
    try:
        conn = get_db()
        try:
            analysis = conn.execute('SELECT moves_json FROM analysis WHERE game_id=?', (game_id,)).fetchone()
        finally:
            conn.close()
        if not analysis:
            return None
        try:
            moves = json.loads(analysis['moves_json'])
        except (json.JSONDecodeError, TypeError):
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
        conn = sqlite3.connect(DB_PATH, timeout=30)
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

# game_id → consecutive commentary failures. Guarded by _analysis_lock.
# Once a game hits MAX_COMMENTARY_RETRIES, we stop auto-retrying on status
# polls; an explicit "redo commentary" resets the counter.
_commentary_failures = {}
MAX_COMMENTARY_RETRIES = 2

# game_id → (positions_done, positions_total) while Stockfish runs.
# Guarded by _analysis_lock; read by the status endpoint for the progress UI.
_analysis_progress = {}

# Bounded pools: "Re-analyze All" used to spawn one thread per game, i.e. dozens
# of concurrent Stockfish processes (CPU thrash) or Claude calls (rate limits).
# A few jobs at a time finishes faster overall and stays stable.
_stockfish_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='stockfish')
_claude_pool    = ThreadPoolExecutor(max_workers=3, thread_name_prefix='claude')


def _maybe_start_analysis(game_id, game, force=False):
    # Even on force, never start a second concurrent run for the same game —
    # two threads would race on the analysis row. The in-flight run still
    # writes a fresh result, which is what force wants anyway.
    with _analysis_lock:
        if game_id in _analysis_in_progress:
            return
        _analysis_in_progress.add(game_id)
    _stockfish_pool.submit(_run_analysis, game_id, game)


def _maybe_start_commentary(game_id, game, moves):
    """Resume just the Claude commentary phase for a game that has Stockfish data."""
    with _analysis_lock:
        if game_id in _analysis_in_progress:
            return
        _analysis_in_progress.add(game_id)
    _claude_pool.submit(_run_commentary, game_id, game, moves)


def _run_commentary(game_id, game, moves):
    """Run only the Claude explanation phase, then update the DB."""
    conn = None
    try:
        played_as = game['played_as']
        username  = game.get('username', CONFIG.get('chesscom_username', ''))
        moves = fill_fast_comments(moves, played_as)
        player_profile = get_player_weakness_summary(username, DB_PATH)
        if needs_claude_commentary(moves, played_as):
            moves = explain_game(
                moves, played_as, CONFIG,
                game_result=game.get('result', 'unknown'),
                player_profile=player_profile,
            )
        # explain_game swallows API errors — detect failure by leftover
        # pending placeholders so claude_ok reflects reality.
        claude_ok = not commentary_incomplete(moves, played_as)
        game_summary = None
        if claude_ok:
            moves = finalize_fast_comments(moves, played_as)
            game_summary = generate_game_summary(
                moves, played_as, CONFIG,
                game_result=game.get('result', 'unknown'),
                player_profile=player_profile,
                opening=game.get('opening'),
            )

        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute(
            'UPDATE analysis SET moves_json=?, claude_ok=?, '
            'game_summary=COALESCE(?, game_summary) WHERE game_id=?',
            (json.dumps(moves), int(claude_ok), game_summary, game_id),
        )
        conn.commit()
        with _analysis_lock:
            if claude_ok:
                _commentary_failures.pop(game_id, None)
            else:
                _commentary_failures[game_id] = _commentary_failures.get(game_id, 0) + 1
        print(f"Commentary complete for {game_id}: claude_ok={claude_ok}")
    except Exception as e:
        with _analysis_lock:
            _commentary_failures[game_id] = _commentary_failures.get(game_id, 0) + 1
        print(f"Commentary failed for {game_id}: {e}")
    finally:
        if conn:
            conn.close()
        with _analysis_lock:
            _analysis_in_progress.discard(game_id)


def _run_analysis(game_id, game):
    conn = None
    try:
        played_as = game['played_as']
        pgn       = game['pgn']

        # Phase 1: Stockfish annotation. Save immediately so the board is
        # viewable while Claude runs in the background.
        def _progress(done, total):
            with _analysis_lock:
                _analysis_progress[game_id] = (done, total)

        moves = annotate_game(pgn, played_as, CONFIG, progress_cb=_progress)
        moves = fill_fast_comments(moves, played_as)
        needs_commentary = needs_claude_commentary(moves, played_as)

        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute(
            'INSERT OR REPLACE INTO analysis (game_id, moves_json, claude_ok) VALUES (?,?,?)',
            (game_id, json.dumps(moves), int(not needs_commentary)),
        )
        conn.execute('UPDATE games SET analyzed = 1 WHERE id = ?', (game_id,))
        conn.commit()
        conn.close()
        conn = None

        # Phase 2: Claude commentary + game summary.
        username = game.get('username', CONFIG.get('chesscom_username', ''))
        player_profile = get_player_weakness_summary(username, DB_PATH)
        if needs_commentary:
            moves = explain_game(
                moves, played_as, CONFIG,
                game_result=game.get('result', 'unknown'),
                player_profile=player_profile,
            )
        # explain_game swallows API errors — detect failure by leftover pending
        # placeholders. On failure keep claude_ok=0 so the status-poll retry
        # path (capped by MAX_COMMENTARY_RETRIES) can pick it up.
        claude_ok = not commentary_incomplete(moves, played_as)
        game_summary = None
        if claude_ok:
            moves = finalize_fast_comments(moves, played_as)
            game_summary = generate_game_summary(
                moves, played_as, CONFIG,
                game_result=game.get('result', 'unknown'),
                player_profile=player_profile,
                opening=game.get('opening'),
            )
        else:
            with _analysis_lock:
                _commentary_failures[game_id] = _commentary_failures.get(game_id, 0) + 1

        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute(
            'UPDATE analysis SET moves_json=?, claude_ok=?, game_summary=? WHERE game_id=?',
            (json.dumps(moves), int(claude_ok), game_summary, game_id),
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
    except Exception as e:
        print(f"Analysis failed for {game_id}: {e}")
    finally:
        if conn:
            conn.close()
        with _analysis_lock:
            _analysis_in_progress.discard(game_id)
            _analysis_progress.pop(game_id, None)


def _auto_gen_patterns(username):
    """Background pattern regeneration triggered automatically every 5 analyzed games."""
    try:
        report = generate_patterns(username, DB_PATH, CONFIG)
        conn = sqlite3.connect(DB_PATH, timeout=30)
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
    # PORT env var takes precedence (used by Claude Code preview proxy); fall back to config.json
    port = int(os.environ.get('PORT', CONFIG.get('port', 5050)))
    # Auto-reload on Python file changes; use_reloader spawns a watcher process.
    # Set CHESS_NO_RELOAD=1 to disable (e.g. when launched headless).
    # debug stays False: the server listens on 0.0.0.0, and the Werkzeug
    # debugger would let anyone on the network execute code.
    use_reloader = os.environ.get('CHESS_NO_RELOAD') != '1'
    print(f"Chess Analyzer → http://localhost:{port} (auto-reload {'on' if use_reloader else 'off'})")
    app.run(
        host='0.0.0.0', port=port, threaded=True,
        debug=False, use_reloader=use_reloader,
    )
