import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"));sys.path.insert(0,str(Path(__file__).parent));import _isolated
from datadistill import speaker_stability as s
def t(a,b,sid):return {'start':a,'end':b,'speaker':sid}
def w(a,b,text=' word'):return {'start':a,'end':b,'word':text}

class Stability(unittest.TestCase):
 def test_numbering_uses_sustained_voice_not_initial_noise(self):
  p,m=s.profiles([t(0,.1,'x'),t(1,5,'y'),t(10,14,'x')])
  self.assertEqual(m,{'y':'S1','x':'S2'})
 def test_short_fragment_remains_tentative_without_forcing_count(self):
  p,m=s.profiles([t(0,5,'a'),t(6,6.2,'b'),t(8,8.3,'b'),t(10,12.5,'c')])
  self.assertEqual(p['b']['status'],'tentative');self.assertNotIn('b',m);self.assertIn('c',m)
 def test_pause_does_not_create_a_new_identity(self):
  ws=[w(1,2),w(40,41)]
  ctx,d=s.annotate(ws,[t(0,4,'a'),t(39,44,'a')]);self.assertEqual(ctx,['S1','S1'])
 def test_uncertain_boundary_keeps_context_but_not_fake_word_certainty(self):
  ws=[w(.2,.6),w(1,1.8),w(1.9,2.3)]
  ctx,d=s.annotate(ws,[t(0,1.1,'a'),t(1.75,5,'a')])
  self.assertEqual(ctx,['S1']*3);self.assertIsNone(ws[1]['attribution']['speaker'])
 def test_real_change_is_not_smoothed_away(self):
  ws=[w(1,2),w(4.5,5.5),w(7,8)]
  ctx,d=s.annotate(ws,[t(0,4,'a'),t(6,10,'b')])
  self.assertEqual(ctx,['S1',None,'S2'])
 def test_overlapping_speech_stays_unassigned(self):
  ws=[w(1,2),w(4,5),w(8,9)]
  ctx,d=s.annotate(ws,[t(0,3,'a'),t(3.5,5.5,'a'),t(3.5,5.5,'b'),t(7,10,'a'),t(12,15,'b')])
  self.assertIsNone(ctx[1]);self.assertEqual(ws[1]['attribution']['status'],'overlap')
 def test_word_in_long_silence_is_not_attributed(self):
  ws=[w(1,2),w(20,21),w(40,41)]
  ctx,d=s.annotate(ws,[t(0,4,'a'),t(39,44,'a')]);self.assertIsNone(ctx[1])
 def test_context_grouping_keeps_every_word_and_uncertainty(self):
  ws=[w(.2,.6,' Hello'),w(1,1.8,' there'),w(1.9,2.3,' again')]
  ctx,_=s.annotate(ws,[t(0,1.1,'a'),t(1.75,5,'a')])
  turns=s.to_turns([{'words':ws,'text':'Hello there again','start':.2,'end':2.3}],ctx)
  self.assertEqual(len(turns),1);self.assertEqual(turns[0]['text'],'Hello there again')
  self.assertEqual(turns[0]['attribution']['uncertain_word_count'],1)
 def test_isolated_brief_change_is_preserved_as_uncertain_event(self):
  ws=[w(1,2),w(3,3.2),w(4,5),w(11,12)]
  ctx,d=s.annotate(ws,[t(0,2.8,'a'),t(3,3.2,'b'),t(3.3,7,'a'),t(10,14,'b')])
  self.assertEqual(ctx[:3],['S1']*3)
  self.assertEqual(ws[1]['attribution']['status'],'possible_interruption')
  self.assertEqual(ws[1]['attribution']['candidate_voice'],'S2')
  self.assertIsNone(ws[1]['attribution']['speaker'])
 def test_uncertain_edges_around_a_long_pause_keep_same_voice_context(self):
  ws=[w(3.2,3.8),w(3.9,4.2),w(39.8,40.1),w(40.2,41)]
  ctx,d=s.annotate(ws,[t(0,3.9,'a'),t(3.9,4.2,'b'),t(39.8,40.1,'b'),t(40.1,44,'a')])
  self.assertEqual(ctx,['S1']*4)
  self.assertIsNone(ws[1]['attribution']['speaker'])
  self.assertIsNone(ws[2]['attribution']['speaker'])
 def test_brief_change_next_to_uncertain_timing_is_still_only_a_candidate(self):
  ws=[w(1,2),w(3,3.6),w(3.6,4),w(4.2,5),w(11,12)]
  ctx,_=s.annotate(ws,[t(0,2.8,'a'),t(3,3.6,'b'),t(4.2,7,'a'),t(10,14,'b')])
  self.assertEqual(ctx[:4],['S1']*4)
  self.assertEqual(ws[1]['attribution']['status'],'possible_interruption')
  self.assertEqual(ws[1]['attribution']['candidate_voice'],'S2')
  self.assertIsNone(ws[1]['attribution']['speaker'])
 def test_uncertain_word_with_overlap_blocks_brief_change_context(self):
  ws=[w(1,2),w(3,3.6),w(3.7,4),w(4.2,5),w(11,12)]
  ctx,_=s.annotate(ws,[t(0,2.8,'a'),t(3,4,'b'),t(3.7,7,'a'),t(10,14,'b')])
  self.assertEqual(ws[1]['attribution']['speaker'],'S2')
  self.assertEqual(ws[2]['attribution']['status'],'overlap')
