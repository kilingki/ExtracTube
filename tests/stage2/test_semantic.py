import unittest
from extractube.semantic import chunks, context, conflict_groups, validate_units
from extractube.candidates import split
from extractube.errors import VisionError

def sentences(n,text='x'):
    return [{'sentence_index':i,'text':text,'source_chunk':{'items':[{'start_time':i,'end_time':i+1}]}} for i in range(n)]

class SemanticTests(unittest.TestCase):
    def test_chunks_40_overlap_8_and_chars(self):
        rows=chunks(sentences(73))
        self.assertEqual([(r['sentence_start'],r['sentence_end']) for r in rows],[(0,40),(32,72),(64,73)])
        rows=chunks(sentences(4,'x'*3000));self.assertTrue(all(r['char_count']<=6000 for r in rows))
        self.assertEqual(chunks([]),[])
        with self.assertRaises(VisionError):chunks(sentences(1,'x'*6001))
    def test_conflict_disagreement_connected_distance_two(self):
        rows=[{'chunk_index':0,'sentence_start':0,'sentence_end':40},{'chunk_index':1,'sentence_start':32,'sentence_end':72}]
        merged=[{'sentence_index':i,'sources':[{'chunk_index':0}]} for i in (10,12,14,35,50)]
        groups=conflict_groups(merged,rows,72)
        self.assertEqual([g['member_indices'] for g in groups],[[10,12,14],[35]])
        self.assertEqual(groups[1]['disagreement_indices'],[35])
    def test_verify_window_tie_trims_right_and_minimum(self):
        ss=sentences(60,'x'*300)
        ctx,left,right=context(ss,0,30,60)
        self.assertEqual(ctx['kind'],'window12');self.assertEqual(len(left),10);self.assertEqual(len(right),10)
        ss=sentences(60,'x'*260)
        ctx,left,right=context(ss,0,30,60)
        self.assertEqual(len(left),12);self.assertEqual(len(right),11)
        with self.assertRaises(VisionError):context(sentences(2,'x'*3001),0,1,2)
    def test_t2_greedy_no_overlap_no_count_limit(self):
        self.assertEqual(split(sentences(100),0,100),[(0,100)])
        self.assertEqual(split(sentences(3,'x'*3000),0,3),[(0,2),(2,3)])
    def test_exact_unit_sentence_preservation(self):
        u={'unit_index':0,'sentence_start':0,'sentence_end':3,'sentence_indices':[0,1,2]}
        validate_units(sentences(3),[u])
        for indices in ([0,0,2],[0,2],[]):
            with self.assertRaises(VisionError):validate_units(sentences(3),[{**u,'sentence_indices':indices}])
