"""Aggregates mistake patterns across multiple analyzed games and generates a coaching report."""

import json
import sqlite3

import anthropic

SYSTEM_PROMPT = (
    "You are a chess coach writing a detailed, specific coaching report for a beginner player (~850 rated). "
    "You have access to real statistics AND actual AI-generated explanations from their analyzed games. "
    "Write 6 specific, actionable coaching observations as bullet points starting with •. "
    "Reference the actual patterns you see in the sample explanations — be concrete. "
    "Don't write generic advice like 'study tactics'. Say things like "
    "'You frequently leave your knight undefended in the middlegame after castling' "
    "if that's what the data shows. "
    "End with one short, genuinely encouraging summary sentence."
)


def generate_patterns(username, db_path, config):
    """Analyze all analyzed games for `username` and return a plain-English coaching report."""
    stats = _aggregate_stats(username, db_path)
    if not stats:
        return "No analyzed games yet. Fetch and analyze some games first."

    api_key = config.get('anthropic_api_key')
    if not api_key:
        return _fallback_report(stats)

    model = config.get('patterns_model', 'claude-opus-4-6')
    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=1400,
            system=SYSTEM_PROMPT,
            messages=[{'role': 'user', 'content': _build_prompt(stats)}],
            timeout=120,
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"Pattern generation failed: {e}")
        return _fallback_report(stats)


def _aggregate_stats(username, db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT g.played_as, g.result, g.time_class, g.opening, g.eco, a.moves_json
               FROM games g
               JOIN analysis a ON g.id = a.game_id
               WHERE g.username = ? AND a.moves_json IS NOT NULL""",
            (username,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return None

    total = len(rows)
    wins  = sum(1 for r in rows if r['result'] == 'win')

    blunders = mistakes = inaccuracies = 0
    phase = {'opening': 0, 'middlegame': 0, 'endgame': 0}

    color_stats = {
        'white': {'wins': 0, 'total': 0, 'blunders': 0},
        'black': {'wins': 0, 'total': 0, 'blunders': 0},
    }

    piece_blunders  = {}   # piece type → count
    time_class_errs = {}
    opening_stats   = {}   # opening name → {'wins': 0, 'total': 0, 'blunders': 0}

    blunder_samples  = []
    mistake_samples  = []
    opp_strong_moves = []

    for row in rows:
        played_as  = row['played_as']
        time_class = row['time_class'] or 'unknown'
        opening    = row['opening'] or 'Unknown'

        color_stats[played_as]['total'] += 1
        if row['result'] == 'win':
            color_stats[played_as]['wins'] += 1

        if opening not in opening_stats:
            opening_stats[opening] = {'wins': 0, 'total': 0, 'blunders': 0}
        opening_stats[opening]['total'] += 1
        if row['result'] == 'win':
            opening_stats[opening]['wins'] += 1

        try:
            moves = json.loads(row['moves_json'])
        except Exception:
            continue

        my_moves  = [m for m in moves if m.get('color') == played_as]
        opp_moves = [m for m in moves if m.get('color') != played_as]

        for m in my_moves:
            c  = m.get('classification', '')
            mn = m.get('move_number', 0)

            if c in ('blunder', 'mistake', 'inaccuracy'):
                if mn <= 12:       # opening: moves 1-12
                    phase['opening'] += 1
                elif mn <= 30:     # middlegame: moves 13-30
                    phase['middlegame'] += 1
                else:
                    phase['endgame'] += 1

                san = m.get('san', '')
                piece = san[0] if san and san[0] in 'NRBQK' else 'P'
                piece_blunders[piece] = piece_blunders.get(piece, 0) + 1
                time_class_errs[time_class] = time_class_errs.get(time_class, 0) + 1

            if c == 'blunder':
                blunders += 1
                color_stats[played_as]['blunders'] += 1
                opening_stats[opening]['blunders'] += 1
                exp = m.get('explanation')
                if exp and len(blunder_samples) < 25:
                    blunder_samples.append(f"Move {mn}: {exp}")
            elif c == 'mistake':
                mistakes += 1
                exp = m.get('explanation')
                if exp and len(mistake_samples) < 15:
                    mistake_samples.append(f"Move {mn}: {exp}")
            elif c == 'inaccuracy':
                inaccuracies += 1

        for m in opp_moves:
            delta = m.get('delta') or 0
            if delta >= 200:
                exp = m.get('explanation')
                if exp and len(opp_strong_moves) < 10:
                    opp_strong_moves.append(f"Move {m.get('move_number','?')}: {exp}")

    return {
        'total_games':           total,
        'win_rate':              round(wins / total * 100),
        'blunders':              blunders,
        'mistakes':              mistakes,
        'inaccuracies':          inaccuracies,
        'avg_blunders_per_game': round(blunders / total, 1),
        'phase':                 phase,
        'color_stats':           color_stats,
        'piece_blunders':        piece_blunders,
        'time_class_errors':     time_class_errs,
        'opening_stats':         opening_stats,
        'blunder_samples':       blunder_samples,
        'mistake_samples':       mistake_samples,
        'opp_strong_moves':      opp_strong_moves,
    }


def _build_prompt(s):
    worst_phase = max(s['phase'], key=s['phase'].get)
    piece_names = {'N': 'knight', 'B': 'bishop', 'R': 'rook', 'Q': 'queen', 'K': 'king', 'P': 'pawn'}

    w = s['color_stats']['white']
    b = s['color_stats']['black']

    lines = [
        f"Player stats across {s['total_games']} analyzed games:",
        f"- Win rate: {s['win_rate']}% overall",
        f"- As white: {w['wins']}/{w['total']} wins ({round(w['wins']/w['total']*100) if w['total'] else 0}%)",
        f"- As black: {b['wins']}/{b['total']} wins ({round(b['wins']/b['total']*100) if b['total'] else 0}%)",
        "",
        "Error breakdown:",
        f"- Blunders: {s['blunders']} total ({s['avg_blunders_per_game']}/game)",
        f"- Mistakes: {s['mistakes']}, Inaccuracies: {s['inaccuracies']}",
        f"- Blunders by phase — Opening: {s['phase']['opening']}, "
        f"Middlegame: {s['phase']['middlegame']}, Endgame: {s['phase']['endgame']}",
        f"- Most blunder-prone phase: {worst_phase}",
    ]

    if s['piece_blunders']:
        sorted_pieces = sorted(s['piece_blunders'].items(), key=lambda x: -x[1])
        lines.append("- Piece types most involved in blunders: " +
                     ', '.join(f"{piece_names.get(p, p)} ({n})" for p, n in sorted_pieces[:4]))

    if s['time_class_errors']:
        worst_tc = max(s['time_class_errors'], key=s['time_class_errors'].get)
        lines.append(f"- Most errors in {worst_tc} games ({s['time_class_errors'][worst_tc]} total errors)")

    # Opening performance (only openings with ≥3 games)
    notable_openings = {k: v for k, v in s['opening_stats'].items()
                        if v['total'] >= 3 and k != 'Unknown'}
    if notable_openings:
        lines += ["", "Opening performance (≥3 games):"]
        for name, st in sorted(notable_openings.items(),
                                key=lambda x: -x[1]['total'])[:6]:
            wr = round(st['wins'] / st['total'] * 100)
            bpg = round(st['blunders'] / st['total'], 1)
            lines.append(f"  • {name}: {st['wins']}/{st['total']} wins ({wr}%), {bpg} blunders/game")

    if s['blunder_samples']:
        lines += [
            "",
            f"Sample blunder explanations from actual games ({len(s['blunder_samples'])} shown):",
        ]
        lines += [f"  • {b}" for b in s['blunder_samples'][:20]]

    if s['mistake_samples']:
        lines += ["", f"Sample mistake explanations ({len(s['mistake_samples'])} shown):"]
        lines += [f"  • {m}" for m in s['mistake_samples'][:10]]

    if s['opp_strong_moves']:
        lines += ["", "Examples of strong opponent moves the player struggled to handle:"]
        lines += [f"  • {o}" for o in s['opp_strong_moves'][:8]]

    lines += [
        "",
        "Based on ALL of the above, write 6 specific bullet point coaching recommendations. "
        "Reference the actual explanations above — look for recurring themes. Be concrete. "
        "If you see repeated tactical motifs in the blunder samples (forks, hanging pieces, pins), "
        "call them out by name. If the opening data shows a pattern, address it."
    ]

    return '\n'.join(lines)


def _fallback_report(s):
    worst_phase = max(s['phase'], key=s['phase'].get)
    return '\n'.join([
        f"Based on {s['total_games']} analyzed games:",
        f"• Win rate: {s['win_rate']}%",
        f"• Average {s['avg_blunders_per_game']} blunders per game ({s['blunders']} total)",
        f"• Most blunders occur in the {worst_phase}",
        f"• {s['mistakes']} mistakes and {s['inaccuracies']} inaccuracies recorded",
    ])
