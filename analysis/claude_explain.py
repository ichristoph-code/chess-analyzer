"""Fast commentary helpers plus selective Claude explanations.

Most moves can be explained well enough from the Stockfish numbers already on
the move object. Claude is reserved for the player's own inaccurate moves,
mistakes, and blunders, where a more specific coaching note is worth the wait.
"""

import json
import re
import anthropic
import chess

SYSTEM_PROMPT = """\
You are a personal chess coach writing commentary for a specific player whose recurring mistakes you know well.

=== REQUIRED COMMENT STRUCTURE — follow this order for every mistake ===
1. PATTERN CALL-OUT (1 sentence, always first): Does this match a weakness in the player profile? If yes, open with: "Again — [pattern name]." or "You've hit your [pattern] problem again." If it's new: "New pattern to watch: [brief name]."
2. WHAT WENT WRONG (1-2 sentences): Explain the mistake in plain English using only the evaluation swing and Stockfish preference.
3. WHAT TO DO INSTEAD (1 sentence): Name the Stockfish preferred move exactly as supplied, but do not explain it by reconstructing piece locations.

Total: 3 sentences max. Short, sharp, personal.

The player profile appears at the top of the user message — it lists their real recurring weaknesses from 27 games. Read it before writing anything.

=== STYLE ===
- Plain English. You may name the played move and the Stockfish preferred move, but do not name exact piece locations.
- Lead with "you" language: "You missed...", "You walked into...", "You left your..."
- Ruthlessly honest but not discouraging.

=== ACCURACY RULES — strictly enforced ===
- Every move block contains a BOARD STATE section listing every piece and its exact square, machine-generated from the FEN. Use this as your ground truth.
- Every move block also contains MOVE FACTS confirming exactly what moved, what was captured, and whether pieces are newly hanging.
- Do NOT use your own memory, the move sequence, or mental reconstruction to place pieces. Only use BOARD STATE and MOVE FACTS.
- You may name a piece on a specific square only if that square appears in BOARD STATE or MOVE FACTS for that move.
- Only name a tactical motif (fork, pin, skewer, etc.) if MOVE FACTS explicitly describes it. If no motif is listed, say "you gave up too much evaluation" or "you missed the steadier move."
- Being less specific is always better than being wrong.

Respond with ONLY valid JSON:
{"comments": [{"idx": <integer>, "comment": "<string>"}, ...]}\
"""

# Moves that get auto-generated comments (no Claude needed)
_AUTO_OPP_COMMENT = "Solid move — no immediate threat created."
_PLAYER_GOOD_COMMENT = "Solid move — you kept the position steady and avoided giving your opponent an immediate tactical target."
_PENDING_DETAIL_TEXT = "The detailed coach note is still being generated."


def fill_fast_comments(moves, played_as):
    """Populate immediate local comments so the UI has useful text right away."""
    if not moves:
        return moves

    for m in moves:
        if m.get('explanation'):
            continue

        if m.get('color') != played_as:
            m['explanation'] = _opponent_comment(m)
            continue

        if _needs_claude(m, played_as):
            m['explanation'] = _quick_mistake_comment(m)
        else:
            m['explanation'] = _PLAYER_GOOD_COMMENT

    return moves


def needs_claude_commentary(moves, played_as):
    """Return True when there are player mistakes worth sending to Claude."""
    return any(_needs_claude(m, played_as) for m in moves or [])


def finalize_fast_comments(moves, played_as):
    """Remove temporary pending language once commentary generation is finished."""
    for m in moves or []:
        if (
            _needs_claude(m, played_as)
            and _PENDING_DETAIL_TEXT in (m.get('explanation') or '')
        ):
            m['explanation'] = _quick_mistake_comment(m, pending=False)
    return moves


def explain_game(moves, played_as, config, game_result='unknown', player_profile=None):
    """Ask Claude only for important player mistakes; keep local notes elsewhere."""
    api_key = config.get('anthropic_api_key')
    if not moves:
        return moves

    fill_fast_comments(moves, played_as)
    if not api_key or not needs_claude_commentary(moves, played_as):
        return moves

    client = anthropic.Anthropic(api_key=api_key)

    try:
        comments = _batch_comments(client, moves, played_as, game_result, config, player_profile)
    except Exception as e:
        print(f"Claude batch explanation failed: {e}")
        return moves

    for item in comments:
        idx     = item.get('idx')
        comment = item.get('comment', '').strip()
        if idx is not None and 0 <= idx < len(moves) and comment:
            moves[idx]['explanation'] = _safe_claude_comment(comment, moves[idx])

    return moves


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _batch_comments(client, moves, played_as, game_result, config, player_profile=None):
    skip_idxs  = {
        idx for idx, move in enumerate(moves)
        if not _needs_claude(move, played_as)
    }
    move_seq   = _build_move_sequence(moves)
    move_block = _build_move_block(moves, played_as, skip_idxs)

    n_requested = sum(1 for m in moves if _needs_claude(m, played_as))
    n_with_engine_data = sum(
        1 for m in moves
        if _needs_claude(m, played_as) and (m.get('candidates') or m.get('pv_line'))
    )
    profile_block = f"{player_profile}\n\n" if player_profile else ""
    user_prompt = (
        f"{profile_block}"
        f"Game: I am playing as {played_as}. Result: {game_result}.\n\n"
        f"Full move sequence (for context):\n{move_seq}\n\n"
        f"Player moves needing detailed commentary ({n_requested} total; {n_with_engine_data} have deep Stockfish data):\n"
        f"All evals are from YOUR perspective as {played_as} (positive = you are better).\n"
        f"VERIFIED FACTS for each move are machine-generated by python-chess from the exact board position — "
        f"they are the only source you may use for piece-square claims.\n"
        f"Phase markers indicate opening/middlegame/endgame boundaries.\n\n"
        f"{move_block}\n\n"
        f"REMINDER: Check each mistake against the player profile above FIRST. "
        f"If it matches a recurring pattern, say so explicitly. "
        f"Use only the VERIFIED FACTS for any positional or tactical claims — never invent piece locations or motifs."
    )

    model = config.get('explain_model', 'claude-sonnet-4-6')
    max_tokens = min(8000, max(1200, n_requested * 450))

    kwargs = {
        'model': model,
        'max_tokens': max_tokens,
        'system': [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        'messages': [{'role': 'user', 'content': user_prompt}],
        'timeout': config.get('explain_timeout', 180),
    }

    # Extended thinking on Opus 4.7 — uses the new adaptive thinking API
    # (the old `enabled` + `budget_tokens` form is rejected with HTTP 400 on this model).
    # Lets the model verify piece locations against BOARD STATE before committing to text.
    if 'opus' in model:
        kwargs['max_tokens'] = max(max_tokens, 4500)
        kwargs['thinking'] = {'type': 'adaptive'}
        kwargs['output_config'] = {'effort': 'high'}

    response = client.messages.create(**kwargs)

    # With extended thinking, content[0] may be a thinking block — find the text block.
    raw = next((b.text for b in response.content if getattr(b, 'type', None) == 'text'), '').strip()

    # Strip markdown fences if Claude adds them
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[1]
        raw = raw.rsplit('```', 1)[0].strip()

    data = json.loads(raw)
    return data.get('comments', [])


def _build_move_sequence(moves):
    """Compact PGN-style string: '1. e4 e5 2. Nf3 Nc6 ...'"""
    parts = []
    i = 0
    while i < len(moves):
        m = moves[i]
        if m['color'] == 'white':
            nxt = moves[i + 1]['san'] if i + 1 < len(moves) else '...'
            parts.append(f"{m['move_number']}. {m['san']} {nxt}")
            i += 2
        else:
            parts.append(f"{m['move_number']}...{m['san']}")
            i += 1
    return ' '.join(parts)


OPENING_CUTOFF    = 12   # moves 1-12
MIDDLEGAME_CUTOFF = 30   # moves 13-30; 31+ are endgame

def _phase(move_number):
    if move_number <= OPENING_CUTOFF:
        return 'opening'
    if move_number <= MIDDLEGAME_CUTOFF:
        return 'middlegame'
    return 'endgame'


def _build_move_block(moves, played_as, skip_idxs=None):
    """All moves with phase markers and rich Stockfish context."""
    skip_idxs  = skip_idxs or set()
    lines = []
    current_phase = None

    for idx, m in enumerate(moves):
        ph = _phase(m['move_number'])
        if ph != current_phase:
            current_phase = ph
            labels = {'opening': 'OPENING (moves 1–12)',
                      'middlegame': 'MIDDLEGAME (moves 13–30)',
                      'endgame': 'ENDGAME (moves 31+)'}
            lines.append(f"\n=== {labels[ph]} ===")

        is_mine = m['color'] == played_as
        label   = 'MY' if is_mine else 'OPP'
        mn      = m['move_number']
        san     = m['san']
        cls     = m.get('classification', 'unknown')
        delta   = m.get('delta') or 0

        if idx in skip_idxs:
            continue

        e_before = m.get('eval_before')
        e_after  = m.get('eval_after')
        eval_str = ''
        if e_before is not None and e_after is not None:
            sb = '+' if e_before >= 0 else ''
            sa = '+' if e_after  >= 0 else ''
            eval_str = f"  eval: {sb}{e_before/100:.1f} → {sa}{e_after/100:.1f}"

        line = f"[{idx}] {label}  {mn}. {san}  [{cls}]{eval_str}  [NEEDS COMMENT]"

        if is_mine and cls in ('blunder', 'mistake', 'inaccuracy') and abs(delta) >= 50:
            line += f"  (lost ~{abs(delta)/100:.1f} pawns)"

        lines.append(line)

        fen_before = m.get('fen_before')
        if fen_before:
            board_state = _full_board_state(fen_before, played_as)
            if board_state:
                lines.append("  BOARD STATE before this move (machine-generated from FEN, 100% accurate):")
                for bl in board_state:
                    lines.append(f"    {bl}")
            facts = _verified_move_facts(m, played_as)
            if facts:
                lines.append("  MOVE FACTS (machine-generated, 100% accurate):")
                for fact in facts:
                    lines.append(f"    - {fact}")

        # Append rich Stockfish data block for critical positions
        # cp values are stored from WHITE's perspective; flip for black so all evals
        # in this block match the mover's-perspective values in VERIFIED FACTS above.
        candidates = m.get('candidates', [])
        pv_line    = m.get('pv_line', [])
        if candidates or pv_line:
            lines.append("  STOCKFISH DATA (evals from YOUR perspective; positive = you are better):")
            cp_sign = 1 if played_as == 'white' else -1
            for rank, c in enumerate(candidates[:3], 1):
                cp_mover = (c.get('cp', 0) or 0) * cp_sign
                sign = '+' if cp_mover >= 0 else ''
                pv_str = ' '.join(c.get('pv_line', [])[:4]) if c.get('pv_line') else ''
                lines.append(f"  #{rank} candidate: {c['san']} (eval {sign}{cp_mover/100:.1f})  continuation: {pv_str}")
            if pv_line and not candidates:
                lines.append(f"  Best continuation: {' '.join(pv_line)}")

    return '\n'.join(lines)


_PIECE_CHARS = {
    chess.PAWN:   ('P', 'p'),
    chess.KNIGHT: ('N', 'n'),
    chess.BISHOP: ('B', 'b'),
    chess.ROOK:   ('R', 'r'),
    chess.QUEEN:  ('Q', 'q'),
    chess.KING:   ('K', 'k'),
}

_PIECE_NAMES = {
    chess.PAWN: 'pawn', chess.KNIGHT: 'knight', chess.BISHOP: 'bishop',
    chess.ROOK: 'rook', chess.QUEEN: 'queen',   chess.KING: 'king',
}

def _piece_char(piece):
    if piece is None:
        return '.'
    white_char, black_char = _PIECE_CHARS[piece.piece_type]
    return white_char if piece.color == chess.WHITE else black_char


def _full_board_state(fen, played_as):
    """Return a list of plain-English lines describing every piece on the board.

    Generated directly from the FEN by python-chess — guaranteed accurate.
    Groups pieces by type so Claude can quickly locate any piece without
    spatial grid parsing.
    """
    try:
        board = chess.Board(fen)
    except Exception:
        return []

    your_color = chess.BLACK if played_as == 'black' else chess.WHITE
    opp_color  = not your_color
    your_label = played_as.capitalize()
    opp_label  = 'White' if played_as == 'black' else 'Black'

    order = [chess.KING, chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN]

    def piece_lines(color, label):
        lines = []
        for pt in order:
            squares = sorted(board.pieces(pt, color), key=lambda s: (chess.square_rank(s), chess.square_file(s)))
            if not squares:
                continue
            sq_names = ', '.join(chess.square_name(s) for s in squares)
            count = len(squares)
            name = _PIECE_NAMES[pt]
            plural = name if count == 1 else name + 's'
            lines.append(f"  {label} {plural}: {sq_names}")
        return lines

    result = [f"Your pieces ({your_label}):"]
    result += piece_lines(your_color, '')
    result.append(f"Opponent's pieces ({opp_label}):")
    result += piece_lines(opp_color, '')
    return result


def _board_ascii(fen, played_as):
    """Standard orientation (rank 8 top, a–h left) plus an unambiguous piece inventory."""
    try:
        board = chess.Board(fen)

        # Board grid — always white-at-bottom so Claude reads it in its trained orientation
        rows = ['  a b c d e f g h']
        for rank in range(7, -1, -1):
            row = f"{rank + 1} " + ' '.join(
                _piece_char(board.piece_at(chess.square(f, rank))) for f in range(8)
            )
            rows.append(row)
        rows.append('  a b c d e f g h')
        rows.append(f'(White pieces uppercase · Black pieces lowercase · You are playing as {played_as})')

        # Explicit piece inventory — unambiguous square names
        your_color = chess.BLACK if played_as == 'black' else chess.WHITE
        opp_color  = not your_color
        your_pieces, opp_pieces = [], []
        for sq in chess.SQUARES:
            piece = board.piece_at(sq)
            if piece is None:
                continue
            entry = f"{_PIECE_NAMES[piece.piece_type]} {chess.square_name(sq)}"
            if piece.color == your_color:
                your_pieces.append(entry)
            else:
                opp_pieces.append(entry)
        rows.append(f"Your pieces:       {', '.join(your_pieces)}")
        rows.append(f"Opponent's pieces: {', '.join(opp_pieces)}")

        return '\n'.join(rows)
    except Exception:
        return ''


def _verified_move_facts(move, played_as):
    """Machine-checked facts Claude can safely repeat without reading the board."""
    fen_before = move.get('fen_before')
    if not fen_before:
        return []

    try:
        board = chess.Board(fen_before)
    except Exception:
        return []

    facts = []
    mover_color = board.turn  # chess.WHITE or chess.BLACK

    played_san = move.get('san')
    played_move = _parse_san_safe(board, played_san)
    if played_move:
        facts.extend(_move_facts(board, played_move, f"Played move ({played_san})"))
        board_after = board.copy()
        board_after.push(played_move)
        # Only report pieces that BECAME newly hanging due to this move
        hanging_before = _hanging_squares(board, mover_color)
        facts.extend(_after_move_facts(board_after, played_move, played_san, mover_color, hanging_before))

    best_san = move.get('best_move')
    best_move = _parse_san_safe(board, best_san)
    if best_move:
        # Only describe what the preferred move does — do not second-guess Stockfish by
        # checking for hanging pieces after it (intentional sacrifices look "hanging").
        facts.extend(_move_facts(board, best_move, f"Stockfish preferred move ({best_san})"))

    e_before = move.get('eval_before')
    e_after  = move.get('eval_after')
    if e_before is not None and e_after is not None:
        sb = '+' if e_before >= 0 else ''
        sa = '+' if e_after  >= 0 else ''
        facts.append(
            f"Evaluation (mover's perspective) changed from {sb}{e_before/100:.1f} to {sa}{e_after/100:.1f} pawns."
        )

    delta = abs(move.get('delta') or 0)
    if delta:
        facts.append(f"Centipawn loss: about {delta/100:.1f} pawns.")

    return facts


def _parse_san_safe(board, san):
    if not san:
        return None
    try:
        return board.parse_san(san)
    except Exception:
        return None


def _hanging_squares(board, color):
    """Return the set of squares where `color`'s pieces are undefended and attacked."""
    opponent = not color
    hanging = set()
    for sq in chess.SQUARES:
        piece = board.piece_at(sq)
        if piece is None or piece.color != color:
            continue
        if board.attackers(opponent, sq) and not board.attackers(color, sq):
            hanging.add(sq)
    return hanging


def _after_move_facts(board_after, move, san, mover_color, hanging_before=None):
    """Facts about NEWLY hanging pieces after a move (pieces that weren't already hanging)."""
    facts = []
    opponent_color = not mover_color
    piece_values = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
                    chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 99}
    hanging_before = hanging_before or set()

    to_sq = move.to_square
    moved_piece = board_after.piece_at(to_sq)

    # Is the piece that just moved now hanging (and wasn't already hanging on its origin)?
    if moved_piece and moved_piece.color == mover_color:
        attackers = board_after.attackers(opponent_color, to_sq)
        defenders = board_after.attackers(mover_color, to_sq)
        if attackers and not defenders:
            facts.append(
                f"After {san}: the moved {_PIECE_NAMES[moved_piece.piece_type]} on "
                f"{chess.square_name(to_sq)} is undefended and can be captured for free (hanging)."
            )
        elif attackers:
            atk_vals = [piece_values.get(
                (board_after.piece_at(a) or chess.Piece(chess.PAWN, opponent_color)).piece_type, 1)
                for a in attackers]
            def_vals = [piece_values.get(
                (board_after.piece_at(d) or chess.Piece(chess.PAWN, mover_color)).piece_type, 1)
                for d in defenders]
            if min(atk_vals) < piece_values.get(moved_piece.piece_type, 1) and min(atk_vals) <= min(def_vals):
                facts.append(
                    f"After {san}: the moved {_PIECE_NAMES[moved_piece.piece_type]} on "
                    f"{chess.square_name(to_sq)} is under attack and may be captured at a loss."
                )

    # Did this move newly expose another piece on the mover's side?
    for sq in chess.SQUARES:
        if sq == to_sq or sq in hanging_before:
            continue
        piece = board_after.piece_at(sq)
        if piece is None or piece.color != mover_color:
            continue
        attackers = board_after.attackers(opponent_color, sq)
        defenders = board_after.attackers(mover_color, sq)
        if attackers and not defenders and piece_values.get(piece.piece_type, 1) >= 3:
            facts.append(
                f"After {san}: your {_PIECE_NAMES[piece.piece_type]} on "
                f"{chess.square_name(sq)} is now undefended and attacked (newly hanging)."
            )

    if board_after.is_check():
        facts.append(f"After {san}: the opponent's king is in check.")

    return facts


def _move_facts(board, move, label):
    piece = board.piece_at(move.from_square)
    if piece is None:
        return []

    piece_name = _PIECE_NAMES[piece.piece_type]
    color_name = 'white' if piece.color == chess.WHITE else 'black'
    from_sq = chess.square_name(move.from_square)
    to_sq = chess.square_name(move.to_square)
    facts = [f"{label}: {color_name} {piece_name} moves from {from_sq} to {to_sq}."]

    captured = board.piece_at(move.to_square)
    if captured:
        captured_name = _PIECE_NAMES[captured.piece_type]
        captured_color = 'white' if captured.color == chess.WHITE else 'black'
        # Check whether the destination square is defended by the opponent — this
        # determines whether the capture is a free piece or an exchange.
        recapturers = list(board.attackers(captured.color, move.to_square))
        if not recapturers:
            facts.append(
                f"{label}: it captures an UNDEFENDED {captured_color} {captured_name} on {to_sq} "
                f"(no recapturer — this wins material outright)."
            )
        else:
            recap_names = ', '.join(
                f"{_PIECE_NAMES[(board.piece_at(s)).piece_type]} on {chess.square_name(s)}"
                for s in recapturers
                if board.piece_at(s) is not None
            )
            facts.append(
                f"{label}: it captures a {captured_color} {captured_name} on {to_sq}, "
                f"but {to_sq} is DEFENDED by {captured_color} {recap_names} — this is an exchange, NOT a free piece."
            )
    elif board.is_en_passant(move):
        captured_sq = chess.square(chess.square_file(move.to_square), chess.square_rank(move.from_square))
        facts.append(f"{label}: it captures en passant on {chess.square_name(captured_sq)}.")

    if move.promotion:
        facts.append(f"{label}: the pawn promotes to a {_PIECE_NAMES[move.promotion]}.")

    if board.is_castling(move):
        side = 'kingside' if chess.square_file(move.to_square) == 6 else 'queenside'
        facts.append(f"{label}: this is {side} castling.")

    if board.gives_check(move):
        facts.append(f"{label}: this gives check.")

    return facts


def _needs_claude(move, played_as):
    return (
        move.get('color') == played_as
        and move.get('classification') in ('inaccuracy', 'mistake', 'blunder')
    )


def _opponent_comment(move):
    cls = move.get('classification')
    delta = abs(move.get('delta') or 0)
    if cls in ('blunder', 'mistake', 'inaccuracy') or delta >= 150:
        return "Your opponent gave you a chance here — check the engine suggestion before moving on."
    return _AUTO_OPP_COMMENT


def _quick_mistake_comment(move, pending=True):
    cls = move.get('classification', 'inaccuracy')
    lost = abs(move.get('delta') or 0) / 100
    best = move.get('best_move')
    article = 'an' if cls[:1].lower() in 'aeiou' else 'a'
    suffix = (
        f" {_PENDING_DETAIL_TEXT}"
        if pending else
        " This is a quick Stockfish note; no deeper coach note was generated for this move."
    )
    if best:
        return (
            f"This was {article} {cls}: Stockfish preferred {best}, and the move played "
            f"cost about {lost:.1f} pawns.{suffix}"
        )
    return (
        f"This was {article} {cls}: the evaluation shifted by about {lost:.1f} pawns."
        f"{suffix}"
    )


_SQUARE_RE = re.compile(r'\b[a-h][1-8]\b', re.IGNORECASE)


_FREE_PIECE_RE = re.compile(
    r'(?:free|simply\s+won|simply\s+wins|wins?\s+(?:a\s+|an\s+)?(?:piece|knight|bishop|rook|queen|pawn)|'
    r'(?:hanging|undefended)\s+(?:knight|bishop|rook|queen|pawn|piece))',
    re.IGNORECASE,
)


def _safe_claude_comment(comment, move):
    """Strip comments that contradict the verified facts."""
    if _has_extra_square_mentions(comment, move):
        return _quick_mistake_comment(move, pending=False)
    if _claims_free_piece_falsely(comment, move):
        return _quick_mistake_comment(move, pending=False)
    return comment


def _claims_free_piece_falsely(comment, move):
    """Reject comments claiming a free win when the captured square is actually defended."""
    if not _FREE_PIECE_RE.search(comment or ''):
        return False
    fen_before = move.get('fen_before')
    best_san = move.get('best_move')
    if not (fen_before and best_san):
        return False
    try:
        board = chess.Board(fen_before)
        best_move = board.parse_san(best_san)
    except Exception:
        return False
    if not board.is_capture(best_move):
        # Comment claims a free piece but the suggested move isn't even a capture
        return True
    captured = board.piece_at(best_move.to_square)
    if captured is None:
        return True
    # If the destination square is defended, the comment is wrong about "free"
    return bool(board.attackers(captured.color, best_move.to_square))


_SAN_SQ_RE = re.compile(r'[a-h][1-8]')   # no word boundaries — works inside SAN like Nxd2+

def _has_extra_square_mentions(comment, move):
    # Collect every square explicitly mentioned in move notation or verified facts
    allowed = set()
    for san in (move.get('san'), move.get('best_move')):
        allowed.update(s.lower() for s in _SAN_SQ_RE.findall(san or ''))
    for c in (move.get('candidates') or []):
        allowed.update(s.lower() for s in _SAN_SQ_RE.findall(c.get('san') or ''))
    # Comments may only mention squares that appear in allowed move notation
    for square in _SQUARE_RE.findall(comment or ''):
        if square.lower() not in allowed:
            return True
    return False
