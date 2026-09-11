import io
import unittest
from unittest.mock import Mock
import chess
import chess.engine
import chess.pgn
from analysis.coaching import move_lesson, enrich_game, priority, phase
from analysis.engine import _annotate_with_engine
from analysis.claude_explain import _opponent_comment, _has_extra_square_mentions

class CoachingTests(unittest.TestCase):
    def move(self, **kw):
        m = dict(fen_before=chess.STARTING_FEN, san='e4', best_move='Nf3', pv_line=['1. Nf3', '1... d5', '2. d4'], color='white', move_number=1, eval_before=20, eval_after=-120, delta=140, classification='inaccuracy')
        m.update(kw)
        return m
    def test_good_move_teaches_played_move_not_engine_alternative(self):
        lesson = move_lesson(self.move(classification='best', eval_after=10, delta=10))
        self.assertEqual(lesson['kind'], 'reinforce')
        self.assertEqual(lesson['candidate'], 'e4')
        self.assertIn('e2 to e4', lesson['evidence'][0])
        self.assertEqual(lesson['comparison']['move'], 'Nf3')
        self.assertEqual(lesson['line_label'], 'Engine alternative')
    def test_inconsistent_best_label_does_not_earn_praise(self):
        lesson = move_lesson(self.move(classification='best', eval_after=-500))
        self.assertNotEqual(lesson['kind'], 'reinforce')
    def test_missing_evaluation_does_not_earn_praise(self):
        lesson = move_lesson(self.move(classification='best', eval_before=None))
        self.assertEqual(lesson['kind'], 'explore')
    def test_game_review_contains_positive_and_improvement_examples(self):
        moves = [self.move(), self.move(classification='best', eval_after=10, delta=10)]
        r = enrich_game(moves, 'white')
        self.assertEqual([t['kind'] for t in r['themes']], ['improve', 'reinforce'])
        self.assertEqual(r['themes'][1]['idx'], 2)
    def test_good_only_game_has_a_supported_lesson(self):
        r = enrich_game([self.move(classification='best', eval_after=10)], 'white')
        self.assertEqual(r['themes'][0]['kind'], 'reinforce')
        self.assertEqual(len(r['moments']), 1)
    def test_illegal_played_move_does_not_create_evidence(self):
        lesson = move_lesson(self.move(san='Qh8'))
        self.assertNotIn('evidence', lesson)
    def test_castling_explains_limits_of_safety(self):
        lesson = move_lesson(self.move(fen_before='r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1', san='O-O', best_move='O-O', pv_line=[]))
        self.assertIn('king and rook', lesson['evidence'][0])
        self.assertIn('not proof', lesson['boundary'])
    def test_check_requires_a_response_even_on_a_capture(self):
        lesson = move_lesson(self.move(fen_before='4k3/8/8/8/8/8/4r3/4K3 w - - 0 1', san='Kxe2', best_move='Kxe2', pv_line=[]))
        self.assertEqual(lesson['concept'], 'Answer the threat before making a plan')
    def test_en_passant_capture_is_named(self):
        lesson = move_lesson(self.move(fen_before='4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1', san='exd6', best_move='exd6', pv_line=[]))
        self.assertIn('en passant', lesson['evidence'][0])
    def test_missing_recommendation_is_not_invented(self):
        lesson = move_lesson(self.move(best_move=None))
        self.assertNotIn('candidate', lesson)
        self.assertEqual(lesson['line'], [])
    def test_study_position_precedes_candidate(self):
        lesson = move_lesson(self.move())
        board = chess.Board(lesson['fen_before'])
        self.assertEqual(board.san(board.parse_san(lesson['candidate'])), 'Nf3')
        self.assertIn('developing square', lesson['question'])
    def test_review_starts_with_highest_priority(self):
        moves = [self.move(eval_after=-100), self.move(move_number=2, eval_after=-700)]
        review = enrich_game(moves, 'white')
        self.assertEqual(review['moments'][0]['idx'], 2)
        self.assertEqual(review['moments'][0]['priority'], 1)
    def test_legal_variation(self):
        lesson = move_lesson(self.move())
        self.assertEqual(len(lesson['line']), 3)
        self.assertEqual(lesson['concept'], 'Develop your pieces')
        self.assertEqual(chess.Board(lesson['line'][0]['fen']).piece_at(chess.F3).piece_type, chess.KNIGHT)
    def test_reject_inconsistent_cached_line(self):
        self.assertEqual(move_lesson(self.move(pv_line=['1. e4']))['line'], [])
    def test_illegal_line_stops(self):
        self.assertEqual(len(move_lesson(self.move(pv_line=['1. Nf3','1... Qh4']))['line']), 1)
    def test_missing_eval_is_not_good_play(self):
        r = enrich_game([self.move(eval_before=None, eval_after=None)], 'white')
        self.assertIn('unavailable', r['summary'])
    def test_meaningful_turning_point(self):
        self.assertGreater(priority(self.move(eval_before=50, eval_after=-500)), priority(self.move(eval_before=-1200, eval_after=-2500)))
    def test_phase_uses_material(self):
        self.assertEqual(phase(self.move(fen_before='8/8/8/8/8/8/P3K3/7k w - - 0 8')), 'endgame')
        self.assertEqual(phase(self.move(move_number=40)), 'middlegame')
    def test_negative_opponent_delta_is_not_opportunity(self):
        self.assertNotIn('gave you a chance', _opponent_comment({'delta': -500}))
    def test_origin_square_allowed(self):
        self.assertFalse(_has_extra_square_mentions('Develop the knight from g1 to f3.', self.move()))
    def test_deep_search_drives_recommendation(self):
        game = chess.pgn.read_game(io.StringIO('1. e4 *'))
        engine = Mock()
        def info(cp, uci):
            return {'score': chess.engine.PovScore(chess.engine.Cp(cp), chess.WHITE), 'pv': [chess.Move.from_uci(uci)]}
        engine.analyse.side_effect = [info(100, 'd2d4'), info(-300, 'e7e5'), [info(80, 'g1f3')], info(-200, 'e7e5')]
        m = _annotate_with_engine(game, engine, 'white', .01)[0]
        self.assertEqual(m['best_move'], 'Nf3')
        self.assertEqual(m['best_move_uci'], 'g1f3')
        self.assertEqual(m['eval_before'], 80)
        self.assertEqual(m['eval_after'], -200)
        self.assertEqual(m['delta'], 280)
        self.assertEqual(m['classification'], 'inaccuracy')

if __name__ == '__main__':
    unittest.main()
