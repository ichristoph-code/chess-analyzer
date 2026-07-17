"""Utilities for converting FEN strings into human-readable position descriptions.

Claude struggles to accurately parse FEN notation mentally, which leads to errors
about piece and pawn locations in commentary and chat. This module converts the FEN
into plain English so Claude always has accurate position facts.
"""

FILES = 'abcdefgh'

PIECE_NAMES = {
    'P': 'White pawn',   'p': 'Black pawn',
    'N': 'White knight', 'n': 'Black knight',
    'B': 'White bishop', 'b': 'Black bishop',
    'R': 'White rook',   'r': 'Black rook',
    'Q': 'White queen',  'q': 'Black queen',
    'K': 'White king',   'k': 'Black king',
}


def _parse_fen_board(fen: str) -> dict[str, str]:
    """Return {square: piece_char} for every occupied square, e.g. {'e4': 'P'}."""
    board_part = fen.split()[0]
    squares: dict[str, str] = {}
    rank = 8
    for row in board_part.split('/'):
        file_idx = 0
        for ch in row:
            if ch.isdigit():
                file_idx += int(ch)
            else:
                sq = FILES[file_idx] + str(rank)
                squares[sq] = ch
                file_idx += 1
        rank -= 1
    return squares


def fen_to_position_summary(fen: str) -> str:
    """Return a compact, human-readable summary of all piece positions from a FEN.

    Example output:
        White pieces: King g1, Queen d1, Rooks a1 h1, Bishops c1 f4, Knights f3, Pawns a2 b2 c3 d4 e5 f2 g2 h2
        Black pieces: King e8, Queen d8, Rooks a8 h8, Bishops c8 f8, Knights g8, Pawns a7 b6 c5 d6 e7 f7 g7 h7
        Side to move: White
    """
    if not fen:
        return ''

    try:
        parts = fen.split()
        squares = _parse_fen_board(fen)
        side_to_move = 'White' if parts[1] == 'w' else 'Black'

        # Group by piece type
        buckets: dict[str, list[str]] = {}
        for sq, pc in sorted(squares.items()):
            buckets.setdefault(pc, []).append(sq)

        def fmt(piece_char: str, label: str) -> str:
            sqs = buckets.get(piece_char, [])
            if not sqs:
                return ''
            return f"{label}{'s' if len(sqs) > 1 else ''}: {' '.join(sqs)}"

        white_parts = [
            fmt('K', 'King'), fmt('Q', 'Queen'), fmt('R', 'Rook'),
            fmt('B', 'Bishop'), fmt('N', 'Knight'), fmt('P', 'Pawn'),
        ]
        black_parts = [
            fmt('k', 'King'), fmt('q', 'Queen'), fmt('r', 'Rook'),
            fmt('b', 'Bishop'), fmt('n', 'Knight'), fmt('p', 'Pawn'),
        ]

        white_str = ', '.join(p for p in white_parts if p)
        black_str = ', '.join(p for p in black_parts if p)

        lines = [
            f"Side to move: {side_to_move}",
            f"White pieces — {white_str}",
            f"Black pieces — {black_str}",
        ]
        return '\n'.join(lines)
    except Exception:
        return ''


def fen_pawn_summary(fen: str) -> str:
    """Return just the pawn locations — useful for a quick sanity-check context line."""
    if not fen:
        return ''
    try:
        squares = _parse_fen_board(fen)
        wp = sorted(sq for sq, pc in squares.items() if pc == 'P')
        bp = sorted(sq for sq, pc in squares.items() if pc == 'p')
        parts = []
        if wp:
            parts.append(f"White pawns: {' '.join(wp)}")
        if bp:
            parts.append(f"Black pawns: {' '.join(bp)}")
        return '  |  '.join(parts)
    except Exception:
        return ''
