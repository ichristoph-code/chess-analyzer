"""Observable move features paired with transferable, conditional principles.

Attack maps describe geometric control (including pinned pieces), not forced wins.
"""
import chess


def name(board, square):
    piece = board.piece_at(square)
    return f'{chess.piece_name(piece.piece_type)} on {chess.square_name(square)}'


def describe(board, move):
    piece = board.piece_at(move.from_square)
    after = board.copy()
    after.push(move)
    san = board.san(move)
    origin, dest = map(chess.square_name, (move.from_square, move.to_square))
    evidence = [f'{san} moves the {chess.piece_name(piece.piece_type)} from {origin} to {dest}.']
    before_attacks = board.attacks(move.from_square)
    after_attacks = after.attacks(move.to_square)
    new_support = [sq for sq in after_attacks if sq not in before_attacks
                   and after.piece_at(sq) and after.color_at(sq) == piece.color
                   and after.piece_type_at(sq) != chess.KING]
    threatened_support = [sq for sq in new_support if board.is_attacked_by(not piece.color, sq)]
    targets = [sq for sq in after_attacks if after.piece_at(sq)
               and after.color_at(sq) != piece.color and after.piece_type_at(sq) != chess.KING]
    center = set([chess.D4, chess.E4, chess.D5, chess.E5])
    gained_center = sorted(center.intersection(after_attacks) - center.intersection(before_attacks))
    lost_support = [sq for sq in before_attacks if board.piece_at(sq)
                    and board.color_at(sq) == piece.color and board.piece_type_at(sq) != chess.KING
                    and after.piece_at(sq) == board.piece_at(sq)
                    and not after.is_attacked_by(piece.color, sq)]
    if targets:
        evidence.append('From its new square it attacks the ' + ', '.join(name(after, sq) for sq in targets[:2]) + '.')
    if new_support:
        evidence.append('It adds protection to the ' + ', '.join(name(after, sq) for sq in new_support[:2]) + '.')
    if gained_center:
        evidence.append('It newly controls ' + ', '.join(map(chess.square_name, gained_center)) + ' in the center.')
    if lost_support:
        evidence.append('After the move, the ' + ', '.join(name(after, sq) for sq in lost_support[:2]) + ' has no friendly defender.')

    concept = 'Give a piece a useful job'
    principle = 'Judge activity by useful targets, protected teammates, and access to important squares, rather than simply moving a piece forward.'
    question = 'What job does this piece have now, and what job could it do after moving?'
    boundary = 'A more active square can still be tactically unsafe. Check the opponent’s checks and captures first.'
    if board.is_check():
        concept = 'Answer the threat before making a plan'
        evidence.insert(0, 'Your king is in check; every legal candidate must resolve that check.')
        principle = 'An immediate threat sets the priorities. First meet it, then compare which safe response best helps your position.'
        question = 'What must I answer immediately, and which legal response also improves my position?'
        boundary = 'This position requires answering check. Other threats may allow a stronger counter-threat, but only after calculation.'
    elif board.is_castling(move):
        concept = 'King safety and rook activity'
        evidence[0] = f'{san} castles: the king and rook move together.'
        principle = 'Connect development with king safety: castling can prepare the rook to participate while relocating the king.'
        question = 'Which side offers my king a safer home, and how will my rook enter the game?'
        boundary = 'Legal castling is not proof of lasting safety. Look at open lines and enemy pieces near the destination.'
    elif board.gives_check(move):
        concept = 'Calculate forcing moves'
        evidence.insert(0, f'{san} gives check, so the opponent must respond to the king threat.')
        principle = 'Forcing moves narrow the opponent’s choices. Calculate the strongest reply and judge the position after the forcing sequence ends.'
        question = 'After my check and their strongest answer, what have I actually improved?'
        boundary = 'Checking is not automatically progress: the reply may develop a piece or drive your attacker away.'
    elif board.is_capture(move):
        concept = 'Calculate the exchange'
        victim = 'pawn en passant' if board.is_en_passant(move) else chess.piece_name(board.piece_type_at(move.to_square))
        evidence.insert(0, f'{san} captures a {victim}.')
        principle = 'Evaluate an exchange by what remains: material, piece activity, pawn structure, and king safety after the replies.'
        question = 'If they recapture, which pieces remain and whose position becomes easier to play?'
        boundary = 'A capture does not by itself win material. Check recaptures and intermediate checks before counting a gain.'
    elif threatened_support:
        concept = 'Defend while improving a piece'
        evidence.insert(0, 'Before the move, the opponent attacks the ' + ', '.join(name(board, sq) for sq in threatened_support[:2]) + '; this move adds a defender.')
        principle = 'Look for moves that solve two problems: reinforce an attacked piece while giving the defender a useful role.'
        question = 'Can I add protection without making my pieces passive?'
        boundary = 'An extra defender is not a guarantee of safety: pins, exchanges, and overloaded defenders still need calculation.'
    elif piece.piece_type in (chess.KNIGHT, chess.BISHOP) and chess.square_rank(move.from_square) == (0 if piece.color else 7):
        concept = 'Develop your pieces'
        principle = 'Bring unused pieces into useful roles so more of your army can influence the position.'
        question = 'Which minor piece is still on its home rank? Find a useful developing square.'
        boundary = 'A piece on its home rank may have moved earlier. Development also takes second place to an immediate threat.'
    elif piece.piece_type == chess.PAWN:
        concept = 'Pawn moves change lasting responsibilities'
        principle = 'A pawn move gains some squares and gives up others. Check both changes because a pawn cannot move backward.'
        question = 'Which squares and pieces lose protection if this pawn advances or captures?'
        boundary = 'More space is useful only if your pieces can support it; pawn advances can leave targets behind.'
    elif piece.piece_type == chess.KING:
        concept = 'Make the king useful without exposing it'
        principle = 'The king can defend and attack nearby targets, but its activity must be balanced against the opponent’s forcing moves.'
        question = 'What can my king help with from this square, and what checks would it face?'
        boundary = 'Reduced material often makes king activity easier, but queens and rooks can still make an exposed king unsafe.'
    elif targets:
        concept = 'Create a threat with a useful move'
        principle = 'A useful threat can make the opponent spend a move responding while improving your piece’s role.'
        question = 'If they meet my threat, is my piece still well placed?'
        boundary = 'An attacked piece is not necessarily a winning target. Check its defenders and the opponent’s counterplay.'
    return dict(concept=concept, principle=principle, evidence=evidence,
                question=question, boundary=boundary, practice=question,
                idea=' '.join(evidence), specificity=len(evidence))
