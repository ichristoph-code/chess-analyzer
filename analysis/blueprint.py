"""Concept-first coaching blueprint built from analyzed game data."""

import json
import sqlite3


CONCEPTS = {
    'hanging_material': {
        'title': 'Stop Leaving Pieces Undefended',
        'triggers': ('hang', 'undefended', 'loose piece', 'free piece', 'lost material'),
        'practice': 'Before every move, scan your pieces and ask which one is least protected.',
    },
    'missed_tactics': {
        'title': 'Look For Basic Tactics',
        'triggers': ('fork', 'pin', 'skewer', 'discovered', 'tactic', 'mate', 'back-rank', 'overloaded'),
        'practice': 'Do 5 minutes of fork, pin, and hanging-piece puzzles before playing.',
    },
    'king_safety': {
        'title': 'Make King Safety Automatic',
        'triggers': ('king', 'castle', 'castling', 'back rank', 'exposed', 'checkmate'),
        'practice': 'In the opening, aim to castle and connect your rooks before starting attacks.',
    },
    'opening_development': {
        'title': 'Develop Before Attacking',
        'triggers': ('develop', 'opening', 'center', 'same piece twice', 'queen out early', 'tempo'),
        'practice': 'For your first 10 moves, count developed minor pieces and center control after each move.',
    },
    'opponent_threats': {
        'title': "Respect Your Opponent's Threats",
        'triggers': ('threat', 'ignored', 'allowed', 'missed', 'respond', 'defend'),
        'practice': 'After your opponent moves, name their threat before choosing your move.',
    },
    'endgame_basics': {
        'title': 'Improve Endgame Decisions',
        'triggers': ('endgame', 'king activity', 'passed pawn', 'promotion', 'trade', 'pawn ending'),
        'practice': 'Practice king-and-pawn endings, especially opposition and passed-pawn races.',
    },
}


PHASE_LABELS = {
    'opening': 'Opening',
    'middlegame': 'Middlegame',
    'endgame': 'Endgame',
}


def generate_blueprint(username, db_path, config=None):
    """Rank observable preferred-move features, never motifs inferred from prose."""
    from analysis.coaching import move_lesson, priority
    rows = _load_analyzed_games(username, db_path)
    buckets = {}
    evaluated_games = 0
    for row in rows:
        try:
            moves = json.loads(row['moves_json'])
        except (TypeError, ValueError):
            continue
        evaluated = [m for m in moves if m.get('color') == row['played_as'] and m.get('eval_after') is not None]
        if not evaluated:
            continue
        evaluated_games += 1
        for m in evaluated:
            if m.get('classification') not in ('inaccuracy', 'mistake', 'blunder') or not m.get('best_move'):
                continue
            lesson = move_lesson(m)
            bucket = buckets.setdefault(lesson['concept'], {'count': 0, 'games': set(), 'weight': 0, 'practice': lesson['practice']})
            bucket['count'] += 1
            bucket['games'].add(row['id'])
            bucket['weight'] += priority(m)
    ranked = sorted(buckets.items(), key=lambda item: (len(item[1]['games']), item[1]['weight']), reverse=True)[:2]
    areas = [{'id': str(i), 'title': concept,
              'why': f"{data['count']} review positions in {len(data['games'])} of {evaluated_games} evaluated games have a preferred move with this feature. This is a practice theme, not a proven cause of the errors.",
              'practice': data['practice']} for i, (concept, data) in enumerate(ranked)]
    return {'ready': bool(evaluated_games),
            'summary': 'Your practice priorities, drawn from the moves in your saved games.' if evaluated_games else 'Analyze a game to build your practice priorities.',
            'focus_areas': areas, 'metrics': [{'label': 'Evaluated games', 'value': str(evaluated_games)}],
            'next_steps': ['Spend five minutes replaying one review position without the engine arrow. Name two candidates and a reply to each, then compare.'] if areas else []}


def _load_analyzed_games(username, db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """SELECT g.id, g.played_as, g.result, g.time_class, g.opening, g.eco, a.moves_json
               FROM games g
               JOIN analysis a ON g.id = a.game_id
               WHERE g.username = ? AND g.analyzed = 1 AND a.moves_json IS NOT NULL
               ORDER BY g.end_time DESC""",
            (username,),
        ).fetchall()
    finally:
        conn.close()


def _collect_stats(rows):
    stats = {
        'games': len(rows),
        'wins': 0,
        'mistakes': 0,
        'blunders': 0,
        'inaccuracies': 0,
        'phase_errors': {'opening': 0, 'middlegame': 0, 'endgame': 0},
        'concepts': {
            key: {'count': 0, 'examples': [], 'phase_counts': {'opening': 0, 'middlegame': 0, 'endgame': 0}}
            for key in CONCEPTS
        },
        'openings': {},
        'time_classes': {},
        'largest_swings': [],
    }

    for row in rows:
        if row['result'] == 'win':
            stats['wins'] += 1

        opening = row['opening'] or 'Unknown opening'
        stats['openings'].setdefault(opening, {'games': 0, 'errors': 0})
        stats['openings'][opening]['games'] += 1

        time_class = row['time_class'] or 'unknown'
        stats['time_classes'].setdefault(time_class, {'games': 0, 'errors': 0})
        stats['time_classes'][time_class]['games'] += 1

        try:
            moves = json.loads(row['moves_json'])
        except Exception:
            continue

        played_as = row['played_as']
        for move in moves:
            if move.get('color') != played_as:
                continue

            classification = move.get('classification')
            if classification not in ('inaccuracy', 'mistake', 'blunder'):
                continue

            phase = _phase(move.get('move_number') or 0)
            stats['phase_errors'][phase] += 1
            stats['openings'][opening]['errors'] += 1
            stats['time_classes'][time_class]['errors'] += 1

            if classification == 'blunder':
                stats['blunders'] += 1
            elif classification == 'mistake':
                stats['mistakes'] += 1
            else:
                stats['inaccuracies'] += 1

            example = _example(row, move)
            text = ' '.join(
                str(move.get(key) or '')
                for key in ('explanation', 'san', 'best_move')
            ).lower()

            matched = False
            for concept_key, concept in CONCEPTS.items():
                if any(trigger in text for trigger in concept['triggers']):
                    _add_concept(stats, concept_key, phase, example)
                    matched = True

            if not matched:
                fallback = 'missed_tactics' if abs(move.get('delta') or 0) >= 250 else 'opponent_threats'
                _add_concept(stats, fallback, phase, example)

            stats['largest_swings'].append({
                'delta': abs(move.get('delta') or 0),
                'move': example,
            })

    stats['largest_swings'].sort(key=lambda item: item['delta'], reverse=True)
    return stats


def _add_concept(stats, concept_key, phase, example):
    bucket = stats['concepts'][concept_key]
    bucket['count'] += 1
    bucket['phase_counts'][phase] += 1
    if len(bucket['examples']) < 3:
        bucket['examples'].append(example)


def _build_focus_areas(stats):
    ranked = sorted(
        stats['concepts'].items(),
        key=lambda item: item[1]['count'],
        reverse=True,
    )
    focus_areas = []
    for key, data in ranked:
        if data['count'] == 0:
            continue
        phase = max(data['phase_counts'], key=data['phase_counts'].get)
        concept = CONCEPTS[key]
        focus_areas.append({
            'id': key,
            'title': concept['title'],
            'why': f"{data['count']} recurring issue(s), mostly in the {PHASE_LABELS[phase].lower()}.",
            'practice': concept['practice'],
        })
    return focus_areas[:4]


def _build_metrics(stats):
    games = max(stats['games'], 1)
    worst_phase = max(stats['phase_errors'], key=stats['phase_errors'].get)
    metrics = [
        {'label': 'Analyzed games', 'value': str(stats['games'])},
        {'label': 'Win rate', 'value': f"{round(stats['wins'] / games * 100)}%"},
        {'label': 'Blunders / game', 'value': f"{stats['blunders'] / games:.1f}"},
        {'label': 'Main trouble phase', 'value': PHASE_LABELS[worst_phase]},
    ]
    return metrics


def _build_next_steps(focus_areas, stats):
    if not focus_areas:
        return [
            'Keep analyzing games until clear patterns emerge.',
            'After each game, review the single biggest evaluation swing.',
        ]

    steps = [
        f"This week, focus on: {focus_areas[0]['title'].lower()}.",
        focus_areas[0]['practice'],
    ]

    if len(focus_areas) > 1:
        steps.append(f"Secondary habit: {focus_areas[1]['practice']}")

    return steps[:4]


def _summary(stats, focus_areas):
    total_errors = stats['blunders'] + stats['mistakes'] + stats['inaccuracies']
    if not total_errors:
        return "Your analyzed games are not showing major recurring mistakes yet. Keep adding games for a clearer picture."
    if focus_areas:
        return (
            f"Across {stats['games']} analyzed game(s), your main improvement theme is "
            f"{focus_areas[0]['title'].lower()}."
        )
    return f"Across {stats['games']} analyzed game(s), Stockfish found {total_errors} teachable moments."


def _example(row, move):
    move_no = move.get('move_number', '?')
    san = move.get('san', '?')
    best = move.get('best_move')
    cls = move.get('classification', 'issue')
    opening = row['opening'] or 'Unknown opening'
    base = f"{move_no}. {san} was a {cls} in {opening}"
    if best and best != san:
        base += f"; Stockfish preferred {best}"
    return base


def _phase(move_number):
    if move_number <= 12:
        return 'opening'
    if move_number <= 30:
        return 'middlegame'
    return 'endgame'
