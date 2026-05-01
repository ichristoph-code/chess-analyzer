"""Single-call batch analysis: one Claude request per game, commentary on every move."""

import json
import anthropic

SYSTEM_PROMPT = """\
You are an expert chess coach analyzing a beginner's game (~850 rated). For each move, write specific, educational commentary using the Stockfish data provided — including candidate moves, evaluations, and the engine's suggested continuation.

=== WHAT YOU HAVE ACCESS TO ===
For every move you will receive:
- The move played and its classification (best/inaccuracy/mistake/blunder)
- Eval before and after (white's POV in pawns)
- For bad moves on critical positions: Stockfish's top candidate moves with their evals, AND the full continuation line (6 moves deep) showing what would have happened after the best move

USE THIS DATA. Don't speak in generalities. Name pieces, squares, tactical ideas. If you have a PV line, explain what it shows.

=== PHASE-SPECIFIC COACHING STANDARDS ===

OPENING (moves 1–12): Focus on development principles.
- Is the player developing pieces toward the center? Castling on time? Fighting for center control?
- Flag specific opening mistakes: moving a piece twice before developing others, weakening pawn structure, ignoring opponent's threats, not castling when possible.

MIDDLEGAME (moves 13–30): Focus on tactics and plans.
- What is each side's strategic plan? Who controls the center or an open file?
- Flag tactical opportunities missed or created. Name the specific tactic if applicable.
- Flag when the player walks into or ignores tactical threats.

ENDGAME (moves 31+): Focus on king activity, pawn promotion, piece coordination.
- Is the king active? Are passed pawns being advanced or stopped?
- Flag missed pawn promotions, king misplacement, wrong piece trades.

=== TACTICAL MOTIF IDENTIFICATION ===
If a blunder or mistake involves a tactical motif, NAME IT explicitly:
- Fork: "This allowed a knight fork — your opponent's knight attacked both your queen and rook simultaneously."
- Pin: "Your bishop is now pinned to your king — it can't move without exposing your king to check."
- Skewer, discovered attack, back-rank mate, hanging piece, overloaded piece — name them when present.

=== HOW TO USE STOCKFISH CANDIDATE MOVES ===
When you have candidates and a PV line, use them to make the explanation concrete:
- Explain WHY the top candidate is better (what threat it creates, what plan it follows)
- If there's a PV continuation, explain what the sequence shows (e.g., "After Nf6, the knight threatens both the queen and a back-rank mate — white is forced to give up material")
- Compare the player's move to the top candidate: what specifically did the player miss?
- If the 2nd/3rd candidates show the range of good options, mention that (e.g., "Both Nf6 and Qd4 win; you played into a tactic instead")

=== COMMENTARY FORMAT ===
For MY moves (player's own moves):
- 3–4 sentences for mistakes/blunders. Be specific and use the engine data.
- 2 sentences for good moves: explain what threat it creates or plan it follows.
- If you have a PV line, reference it: "Stockfish shows that after Nf6 Qe2 Rxd4, white wins the queen."

For OPP moves (opponent's moves):
- Exactly 1 sentence. State the threat or plan created, or note the blunder if significant.
- If the opponent blundered: "Your opponent blundered — [specific threat] was available here."

=== STYLE RULES ===
- Plain English only. No algebraic notation in the explanation text — use piece names and squares ("your knight on f6", "the f-file", "their bishop on b5").
- Reference the evaluation when it matters: "This move flipped a +2 advantage into a -1 position — a 3-pawn swing."
- Be encouraging but ruthlessly honest. Never sugarcoat a blunder.
- Don't waste words hedging. Every sentence should teach something.

Respond with ONLY valid JSON, no markdown fences, no extra text:
{"comments": [{"idx": <integer>, "comment": "<string>"}, ...]}\
"""

# Moves that get auto-generated comments (no Claude needed)
_AUTO_OPP_COMMENT = "Solid move — no immediate threat created."


def explain_game(moves, played_as, config, game_result='unknown'):
    """One Claude call for the whole game. Populates 'explanation' on every move."""
    api_key = config.get('anthropic_api_key')
    if not api_key or not moves:
        return moves

    client = anthropic.Anthropic(api_key=api_key)

    # Pre-fill auto-generated comments for trivial opponent moves
    skip_idxs = _auto_fill_trivial_opp(moves, played_as)

    try:
        comments = _batch_comments(client, moves, played_as, game_result, skip_idxs, config)
    except Exception as e:
        print(f"Claude batch explanation failed: {e}")
        return moves

    for item in comments:
        idx     = item.get('idx')
        comment = item.get('comment', '').strip()
        if idx is not None and 0 <= idx < len(moves) and comment:
            moves[idx]['explanation'] = comment

    return moves


def _auto_fill_trivial_opp(moves, played_as):
    """Auto-comment routine opponent moves so Claude doesn't have to.

    A move is 'trivial' (auto-filled) if it's the opponent's move AND:
      - classified as 'best' (Stockfish confirms no big swing), AND
      - eval swing is small (<= 30cp), AND
      - not in the opening phase (opening moves still get real context)

    Returns the set of idx values that were auto-filled (to exclude from Claude).
    """
    skip = set()
    for idx, m in enumerate(moves):
        if m['color'] == played_as:
            continue  # always send player's own moves to Claude
        cls   = m.get('classification', 'unknown')
        delta = abs(m.get('delta') or 0)
        phase = _phase(m['move_number'])
        if phase == 'opening':
            continue
        if cls in ('blunder', 'mistake', 'inaccuracy'):
            continue
        if cls == 'best' and delta <= 30:
            m['explanation'] = _AUTO_OPP_COMMENT
            skip.add(idx)
    return skip


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _batch_comments(client, moves, played_as, game_result, skip_idxs, config):
    skip_idxs  = skip_idxs or set()
    move_seq   = _build_move_sequence(moves)
    move_block = _build_move_block(moves, played_as, skip_idxs)

    n_requested = sum(1 for i in range(len(moves)) if i not in skip_idxs)
    n_with_engine_data = sum(
        1 for i, m in enumerate(moves)
        if i not in skip_idxs and (m.get('candidates') or m.get('pv_line'))
    )
    user_prompt = (
        f"Game: I am playing as {played_as}. Result: {game_result}.\n\n"
        f"Full move sequence (for context):\n{move_seq}\n\n"
        f"Moves needing commentary ({n_requested} total; {n_with_engine_data} have deep Stockfish data):\n"
        f"Stockfish evals are from WHITE's perspective in pawns.\n"
        f"For critical positions you have: top candidate moves with evals, plus a 6-move continuation line.\n"
        f"Phase markers indicate opening/middlegame/endgame boundaries.\n\n"
        f"{move_block}\n\n"
        f"Write a comment for every move marked [NEEDS COMMENT]. "
        f"Do NOT write comments for moves marked [SKIP]. "
        f"For moves with STOCKFISH DATA blocks, USE that data to write specific, concrete analysis. "
        f"Explain the candidate moves and what the PV line demonstrates. "
        f"Apply the phase-specific coaching standards and identify tactical motifs by name."
    )

    model = config.get('explain_model', 'claude-sonnet-4-6')
    response = client.messages.create(
        model=model,
        max_tokens=12000,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{'role': 'user', 'content': user_prompt}],
        timeout=240,   # 4-minute hard cap (deeper analysis takes longer)
    )

    raw = response.content[0].text.strip()

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


def _phase(move_number):
    if move_number <= 12:
        return 'opening'
    if move_number <= 30:
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
            lines.append(f"[{idx}] {label}  {mn}. {san}  [SKIP]")
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

        # Append rich Stockfish data block for critical positions
        candidates = m.get('candidates', [])
        pv_line    = m.get('pv_line', [])
        if candidates or pv_line:
            lines.append("  ── STOCKFISH DATA ──")
            for rank, c in enumerate(candidates[:3], 1):
                cp = c.get('cp', 0)
                sign = '+' if cp >= 0 else ''
                pv_str = ' '.join(c.get('pv_line', [])[:4]) if c.get('pv_line') else ''
                lines.append(f"  #{rank} candidate: {c['san']} (eval {sign}{cp/100:.1f})  continuation: {pv_str}")
            if pv_line and not candidates:
                lines.append(f"  Best continuation: {' '.join(pv_line)}")

    return '\n'.join(lines)
