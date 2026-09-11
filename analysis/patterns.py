"""Aggregates mistake patterns across multiple analyzed games and generates a coaching report."""

import datetime
import json
import sqlite3

import anthropic

SYSTEM_PROMPT = (
    "Never infer playing speed from error counts or claim a format is harmful without exposure-adjusted rates. "
    "A moved piece is not necessarily the piece lost. Historical explanations are unverified, not proof of tactical motifs. "
    "Do not recommend captures just because they use a pawn, promise rating gains, or invent recurrence counts. "

    "You are a patient chess coach for a low-level adult player who wants big-picture guidance. "
    "Stockfish has already identified the objective mistakes; your job is to turn those facts into concepts, habits, and a practice plan. "
    "Write up to 3 evidence-supported, actionable coaching observations as bullet points starting with •. "
    "Each bullet should name the recurring concept first, then explain how it showed up in the player's games. "
    "Use beginner-friendly chess language: hanging pieces, missed threats, development, king safety, trades, pawn structure, and simple tactics. "
    "Reference actual sample explanations when they reveal a pattern, but do not drown the player in engine lines. "
    "Avoid generic advice like 'study tactics'; instead say exactly what habit to practice before each move. "
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

    model = config.get('patterns_model', 'claude-sonnet-4-6')
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
            """SELECT g.played_as, g.result, g.time_class, g.opening, g.eco,
                      g.opponent, g.end_time, a.moves_json
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

        # Build a short game label for sample citations
        _date = datetime.datetime.fromtimestamp(row['end_time']).strftime('%-m/%-d') if row['end_time'] else '?'
        _game_label = f"vs {row['opponent'] or '?'} ({_date}, {row['result']})"

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
                    blunder_samples.append(f"[{_game_label}] Move {mn}: {exp}")
            elif c == 'mistake':
                mistakes += 1
                exp = m.get('explanation')
                if exp and len(mistake_samples) < 15:
                    mistake_samples.append(f"[{_game_label}] Move {mn}: {exp}")
            elif c == 'inaccuracy':
                inaccuracies += 1

        for m in opp_moves:
            delta = m.get('delta') or 0
            if delta <= -200:
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
        f"- Phase with the most recorded errors (not adjusted for moves played): {worst_phase}",
    ]

    if s['piece_blunders']:
        sorted_pieces = sorted(s['piece_blunders'].items(), key=lambda x: -x[1])
        lines.append("- Piece types most involved in blunders: " +
                     ', '.join(f"{piece_names.get(p, p)} ({n})" for p, n in sorted_pieces[:4]))

    if s['time_class_errors']:
        worst_tc = max(s['time_class_errors'], key=s['time_class_errors'].get)
        lines.append(f"- Largest raw error count in {worst_tc} games ({s['time_class_errors'][worst_tc]} total errors)")

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
        "Based on ALL of the above, write up to 3 evidence-supported coaching recommendations. "
        "Reference the actual explanations above — look for recurring themes. Be concrete. "
        "If you see repeated tactical motifs in the blunder samples (forks, hanging pieces, pins), "
        "call them out by name. If the opening data shows a pattern, address it."
    ]

    return '\n'.join(lines)


def get_player_weakness_summary(username, db_path, max_games=25):
    """Return a compact text block of the player's known weakness patterns.

    Used to personalise per-game Claude explanations without an extra API call.
    Returns None when there is not enough history yet.
    """
    stats = _aggregate_stats(username, db_path)
    if not stats or stats['total_games'] < 3:
        return None

    piece_names = {'N': 'knight', 'B': 'bishop', 'R': 'rook', 'Q': 'queen', 'K': 'king', 'P': 'pawn'}
    worst_phase = max(stats['phase'], key=stats['phase'].get)
    ph = stats['phase']

    lines = [
        f"=== THIS PLAYER'S RECURRING WEAKNESSES (across {stats['total_games']} analyzed games) ===",
        f"Win rate: {stats['win_rate']}%  |  {stats['avg_blunders_per_game']} blunders/game on average",
        f"Blunders by phase — Opening: {ph['opening']}, Middlegame: {ph['middlegame']}, Endgame: {ph['endgame']}  →  most errors in the {worst_phase}",
    ]

    if stats['piece_blunders']:
        sorted_pieces = sorted(stats['piece_blunders'].items(), key=lambda x: -x[1])
        lines.append("Pieces moved on error moves (not necessarily pieces lost): " +
                     ', '.join(f"{piece_names.get(p, p)} ({n}×)" for p, n in sorted_pieces[:4]))

    w = stats['color_stats']['white']
    b = stats['color_stats']['black']
    if w['total'] and b['total']:
        lines.append(
            f"As white: {round(w['wins']/w['total']*100)}% win rate  |  "
            f"As black: {round(b['wins']/b['total']*100)}% win rate"
        )

    recent_blunders = stats['blunder_samples'][:8]
    if recent_blunders:
        lines.append("")
        lines.append("Recent blunder patterns (use these to identify if the current mistake is recurring):")
        for b_sample in recent_blunders:
            lines.append(f"  • {b_sample}")

    recent_mistakes = stats['mistake_samples'][:5]
    if recent_mistakes:
        lines.append("")
        lines.append("Recent mistake patterns:")
        for m_sample in recent_mistakes:
            lines.append(f"  • {m_sample}")

    lines += [
        "",
        "If the move being explained matches a recurring pattern above, explicitly say so: "
        "'This resembles an earlier review example.' Do not invent frequencies.",
        "If it's a new type of error, note that too.",
        "=== END PLAYER PROFILE ===",
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
