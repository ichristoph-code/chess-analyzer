"""Evidence-based coaching available immediately, including for cached games.

Descriptions identify observable move features, not unproven tactical outcomes.
"""
import math
import re
import chess
from analysis.concepts import describe


def phase(move):
    try:
        board = chess.Board(move['fen_before'])
        material = sum(len(board.pieces(p, c)) * v for c in chess.COLORS
                       for p, v in [(chess.KNIGHT, 3), (chess.BISHOP, 3), (chess.ROOK, 5), (chess.QUEEN, 9)])
        if material <= 20:
            return 'endgame'
    except (ValueError, KeyError):
        pass
    return 'opening' if move.get('move_number', 1) <= 12 else 'middlegame'


def position_label(cp):
    if cp is None:
        return 'not evaluated'
    if abs(cp) >= 2900:
        return 'a mating evaluation in your favor' if cp > 0 else 'a mating evaluation against you'
    if cp > 150:
        return 'an advantage'
    if cp < -150:
        return 'a disadvantage'
    return 'a roughly balanced position'


def priority(move):
    """Prefer changes in practical prospects over huge losses in lost positions."""
    before, after = move.get('eval_before'), move.get('eval_after')
    if before is None or after is None:
        return 0
    def chance(cp):
        return 1 / (1 + math.exp(-max(-60, min(60, cp / 250))))
    return max(0, chance(before) - chance(after))


def move_lesson(move):
    try:
        board = chess.Board(move['fen_before'])
        best = board.parse_san(move['best_move'])
    except (KeyError, ValueError, TypeError):
        return {'concept': 'Compare candidate moves', 'idea': 'No verified engine recommendation is available for this position.',
                'practice': 'Compare two legal candidates and the strongest reply to each.', 'line': []}
    try:
        played = board.parse_san(move['san'])
    except (KeyError, ValueError, TypeError):
        return {'concept': 'Compare candidate moves', 'idea': 'The saved played move is invalid.',
                'practice': 'Review a position with valid move data.', 'line': []}
    evaluated = move.get('eval_before') is not None and move.get('eval_after') is not None
    loss = max(0, move['eval_before'] - move['eval_after']) if evaluated else None
    good = evaluated and loss <= 50 and move.get('classification') == 'best'
    focus = played if good else best
    lesson = describe(board, focus)
    lesson['kind'] = 'reinforce' if good else 'improve' if evaluated and move.get('classification') in ('inaccuracy', 'mistake', 'blunder') else 'explore'
    if good:
        lesson['assessment'] = f'Your {board.san(played)} kept the engine evaluation within half a pawn of its preferred result. This is a decision worth studying, even if you did not choose it for this reason.'
    elif evaluated:
        lesson['assessment'] = f'You played {board.san(played)}; Stockfish preferred {board.san(best)}. The features below explain a useful concept to compare, not the complete cause of the evaluation difference.'
    else:
        lesson['assessment'] = 'The move features can be studied, but the saved evaluations are insufficient to assess this decision.'
    if played != best:
        other = describe(board, best if good else played)
        lesson['comparison'] = {
            'label': 'Engine alternative' if good else 'Your move',
            'move': board.san(best if good else played),
            'evidence': other['evidence'][:3],
        }
    # Replay every continuation on its own board; never trust arbitrary SAN text.
    line, b = [], board.copy()
    for raw in (move.get('pv_line') or [])[:6]:
        try:
            san = re.sub(r'^\d+\.{1,3}\s*', '', raw).strip()
            candidate = b.parse_san(san)
            if not line and candidate != best:
                break
            label = f"{b.fullmove_number}{'.' if b.turn else '…'} {b.san(candidate)}"
            b.push(candidate)
            line.append({'san': label, 'fen': b.fen()})
        except (ValueError, TypeError):
            break
    lesson.update(line=line, candidate=board.san(focus), fen_before=board.fen(),
                  line_label='Engine alternative' if focus != best else 'Engine continuation')
    return lesson



def enrich_game(moves, played_as):
    for move in moves:
        move['lesson'] = move_lesson(move)
    mine = [(i, m) for i, m in enumerate(moves) if m.get('color') == played_as and m.get('eval_after') is not None]
    if not mine:
        return {'summary': 'Engine evaluation is unavailable; no assessment of your play can be made yet.', 'moments': [], 'practice': 'Analyze this game with Stockfish to build a review.'}
    errors = [(i, m) for i, m in mine if m.get('classification') in ('inaccuracy', 'mistake', 'blunder')]
    ranked = sorted(errors, key=lambda im: (priority(im[1]), im[1].get('delta') or 0), reverse=True)[:3]
    opening_moves = [m for _, m in mine if phase(m) == 'opening']
    opening = f"By move {opening_moves[-1]['move_number']}, you had {position_label(opening_moves[-1]['eval_after'])}. " if opening_moves else ''
    if ranked:
        _, key = ranked[0]
        summary = opening + f"The priority for review is {key['move_number']}{'.' if played_as == 'white' else '…'} {key['san']}: you went from {position_label(key.get('eval_before'))} to {position_label(key.get('eval_after'))}."
        practice = key['lesson']['practice']
    else:
        summary = opening + 'No moves crossed the configured error thresholds. Review your plans as well as your calculation; this does not mean every move was optimal.'
        practice = 'Choose one quiet position and explain which piece you wanted to improve and why.'
    positives = [(i, m) for i, m in mine if m['lesson'].get('kind') == 'reinforce']
    positive = max(positives, key=lambda im: (im[1]['lesson']['specificity'], -abs(im[1]['eval_before'])), default=None)
    selected = ranked[:2] + ([positive] if positive else [])
    themes = []
    for kind, pair in [('improve', ranked[0] if ranked else None), ('reinforce', positive)]:
        if pair:
            i, m = pair
            lesson = m['lesson']
            themes.append({'kind': kind, 'concept': lesson['concept'], 'principle': lesson.get('principle', lesson['practice']),
                           'practice': lesson['practice'], 'idx': i + 1,
                           'label': f"{m['move_number']}{'.' if played_as == 'white' else '…'} {m['san']}"})
    moments = [{'idx': i + 1, 'label': f"{m['move_number']}{'.' if played_as == 'white' else '…'} {m['san']}",
                'concept': m['lesson']['concept'], 'phase': phase(m), 'priority': n + 1, 'kind': m['lesson'].get('kind', 'explore')} for n, (i, m) in enumerate(selected)]
    return {'summary': summary, 'moments': moments, 'practice': practice, 'themes': themes,
            'coverage': f'{len(mine)} evaluated player moves · {len(errors)} review moments'}
