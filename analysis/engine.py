"""Stockfish wrapper: annotates a PGN with centipawn evaluations and move classifications."""

import io

import chess
import chess.engine
import chess.pgn

# Centipawn thresholds for classification.
INACCURACY_THRESHOLD = 100
MISTAKE_THRESHOLD    = 300
BLUNDER_THRESHOLD    = 600

# For adaptive depth: positions where the player lost this much (cp) get deeper analysis.
CRITICAL_SWING_THRESHOLD = 200


def annotate_game(pgn_str, played_as, config):
    """Parse a PGN string and return a list of annotated move dicts.

    Each dict contains:
        move_number, color, san, fen (after move), fen_before,
        eval_before, eval_after, delta, classification,
        best_move, best_move_uci, pv_line, candidates, explanation
    """
    game = chess.pgn.read_game(io.StringIO(pgn_str))
    if game is None:
        return []

    stockfish_path = config.get('stockfish_path', '/opt/homebrew/bin/stockfish')
    move_time = config.get('stockfish_move_time', 0.2)

    try:
        with chess.engine.SimpleEngine.popen_uci(stockfish_path) as engine:
            return _annotate_with_engine(game, engine, played_as, move_time)
    except FileNotFoundError:
        print(f"Stockfish not found at {stockfish_path}. Install with: brew install stockfish")
        return _annotate_no_engine(game, played_as)


def _annotate_with_engine(game, engine, played_as, move_time):
    """Two-pass analysis:
      Pass 1 (fast): evaluate every position to find the centipawn swings.
      Pass 2 (selective): re-analyze critical positions with Multi-PV and longer time
                          to get candidate moves and PV continuations.
    """
    moves = []
    board = game.board()
    nodes = list(game.mainline())
    if not nodes:
        return []

    # ── Pass 1: fast single-PV sweep ──
    fens = [board.fen()]
    info = engine.analyse(board, chess.engine.Limit(time=move_time))
    scores = [_to_cp(info['score'], chess.WHITE)]
    best_ucis = [info.get('pv', [None])[0]]

    board_pass1 = board.copy()
    for node in nodes:
        board_pass1.push(node.move)
        info = engine.analyse(board_pass1, chess.engine.Limit(time=move_time))
        scores.append(_to_cp(info['score'], chess.WHITE))
        best_ucis.append(info.get('pv', [None])[0])
        fens.append(board_pass1.fen())

    # ── Identify critical positions (big swings on the player's moves) ──
    critical = set()
    for i, node in enumerate(nodes):
        mover = 'white' if chess.Board(fens[i]).turn == chess.WHITE else 'black'
        if mover != played_as:
            continue
        delta = (scores[i] - scores[i+1]) if mover == 'white' else (scores[i+1] - scores[i])
        if delta >= CRITICAL_SWING_THRESHOLD:
            critical.add(i)  # re-analyse position BEFORE this move (fens[i])

    # ── Pass 2: deep Multi-PV for critical positions ──
    deep_data = {}   # index → {candidates, pv_line}
    critical_time = max(move_time * 4, 0.5)   # at least 0.5s, up to 4× base time
    for i in critical:
        try:
            b = chess.Board(fens[i])
            infos = engine.analyse(b, chess.engine.Limit(time=critical_time), multipv=3)
            if not isinstance(infos, list):
                infos = [infos]
            candidates = []
            for info in infos:
                pv = info.get('pv', [])
                if not pv:
                    continue
                try:
                    san = b.san(pv[0])
                except Exception:
                    continue
                cp = _to_cp(info['score'], chess.WHITE)
                pv_line = _pv_to_san(b, pv, max_len=6)
                candidates.append({'san': san, 'cp': cp, 'pv_line': pv_line})
            deep_data[i] = {
                'candidates': candidates,
                'pv_line': candidates[0]['pv_line'] if candidates else [],
            }
        except Exception as e:
            print(f"Deep analysis failed at move {i}: {e}")

    # ── Build final move list ──
    board2 = board.copy()
    for move_index, node in enumerate(nodes):
        move      = node.move
        fen_before = fens[move_index]
        mover     = 'white' if board2.turn == chess.WHITE else 'black'
        is_my_move = (mover == played_as)

        best_uci  = best_ucis[move_index]
        try:
            best_move_san = board2.san(best_uci) if best_uci else None
        except Exception:
            best_move_san = None
        best_move_uci_str = best_uci.uci() if best_uci else None

        san = board2.san(move)
        board2.push(move)
        fen_after = board2.fen()

        score_before = scores[move_index]
        score_after  = scores[move_index + 1]
        delta = (score_before - score_after) if mover == 'white' else (score_after - score_before)

        deep = deep_data.get(move_index, {})

        moves.append({
            'move_number': (move_index // 2) + 1,
            'color': mover,
            'san': san,
            'fen': fen_after,
            'fen_before': fen_before,
            'eval_before': score_before if mover == 'white' else -score_before,
            'eval_after':  score_after  if mover == 'white' else -score_after,
            'delta': delta,
            'classification': _classify(delta, is_my_move),
            'best_move': best_move_san,
            'best_move_uci': best_move_uci_str,
            'pv_line': deep.get('pv_line', []),         # best continuation (SAN list)
            'candidates': deep.get('candidates', []),   # top 3 candidate moves with scores
            'explanation': None,
        })

    return moves


def _pv_to_san(board, pv_moves, max_len=6):
    """Convert a PV move list to SAN strings. Does not modify the original board."""
    b = board.copy()
    sans = []
    move_num = b.fullmove_number
    turn = b.turn
    for move in pv_moves[:max_len]:
        try:
            prefix = f"{move_num}{'.' if turn == chess.WHITE else '...'} "
            sans.append(prefix + b.san(move))
            b.push(move)
            if turn == chess.BLACK:
                move_num += 1
            turn = not turn
        except Exception:
            break
    return sans


def _annotate_no_engine(game, played_as):
    """Return moves without evaluation when Stockfish is unavailable."""
    moves = []
    board = game.board()
    move_index = 0

    for node in game.mainline():
        move = node.move
        fen_before = board.fen()
        mover = 'white' if board.turn == chess.WHITE else 'black'
        san = board.san(move)
        board.push(move)

        moves.append({
            'move_number': (move_index // 2) + 1,
            'color': mover,
            'san': san,
            'fen': board.fen(),
            'fen_before': fen_before,
            'eval_before': None,
            'eval_after': None,
            'delta': None,
            'classification': 'unknown',
            'best_move': None,
            'best_move_uci': None,
            'pv_line': [],
            'candidates': [],
            'explanation': None,
        })
        move_index += 1

    return moves


def get_best_move(fen, config):
    """Return Stockfish's best move for a position as a SAN string, or None on failure."""
    stockfish_path = config.get('stockfish_path', '/opt/homebrew/bin/stockfish')
    move_time = config.get('stockfish_move_time', 0.2)
    try:
        board = chess.Board(fen)
        with chess.engine.SimpleEngine.popen_uci(stockfish_path) as engine:
            infos = engine.analyse(board, chess.engine.Limit(time=move_time), multipv=1)
            if isinstance(infos, list):
                info = infos[0]
            else:
                info = infos
            pv = info.get('pv', [])
            if not pv:
                return None, None
            san = board.san(pv[0])
            cp = _to_cp(info['score'], chess.WHITE)
            return san, cp
    except Exception as e:
        print(f"get_best_move failed: {e}")
    return None, None


def _to_cp(score, perspective):
    """Convert a Stockfish score to centipawns from `perspective` (chess.WHITE or chess.BLACK)."""
    try:
        return score.pov(perspective).score(mate_score=3000)
    except Exception:
        return 0


def _classify(delta, is_my_move):
    """Classify a move based on centipawn loss. Only classify the player's own moves."""
    if not is_my_move or delta is None or delta <= 0:
        return 'best'
    if delta >= BLUNDER_THRESHOLD:
        return 'blunder'
    if delta >= MISTAKE_THRESHOLD:
        return 'mistake'
    if delta >= INACCURACY_THRESHOLD:
        return 'inaccuracy'
    return 'best'
