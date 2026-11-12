import copy
import unittest
from run import identity, metrics, validate_selection

class SelectionTests(unittest.TestCase):
    def test_later_complete_frame_can_replace_prior(self):
        rows=[dict(index=0,decision='omit',covered_by=1,reason='completed state'),
              dict(index=1,decision='keep',covered_by=None,reason='complete')]
        self.assertEqual(validate_selection({'frames':rows},2),rows)

    def test_transitive_substitution_flattened_to_keep(self):
        rows=[dict(index=0,decision='omit',covered_by=1,reason='step 0'),
              dict(index=1,decision='omit',covered_by=2,reason='step 1'),
              dict(index=2,decision='keep',covered_by=None,reason='step 2')]
        res=validate_selection({'frames':rows},3)
        self.assertEqual(res[0]['covered_by'],2)
        self.assertEqual(res[1]['covered_by'],2)
        self.assertEqual(res[2]['decision'],'keep')

    def test_cycles_uncertain_targets_and_missing_indices_rejected(self):
        cases=[
            [dict(index=0,decision='omit',covered_by=1,reason='a'),dict(index=1,decision='omit',covered_by=0,reason='b')],
            [dict(index=0,decision='omit',covered_by=1,reason='a'),dict(index=1,decision='uncertain',covered_by=None,reason='b')],
            [dict(index=0,decision='keep',covered_by=None,reason='a'),dict(index=0,decision='keep',covered_by=None,reason='b')]]
        for rows in cases:
            with self.assertRaises(ValueError): validate_selection({'frames':rows},2)

    def test_body_identity_excludes_only_visual_selection(self):
        a={'sections':[{'title':'A','paragraphs':[{'text':'keep facts','source_indices':[1],
             'paragraph_id':'p','figures':[{'asset_id':'a'},{'asset_id':'b'}]}]}], 'asset_placements':[]}
        b=copy.deepcopy(a)
        b['sections'][0]['paragraphs'][0]['figures'].pop()
        self.assertEqual(identity(a),identity(b))
        b['sections'][0]['paragraphs'][0]['text']='altered'
        self.assertNotEqual(identity(a),identity(b))
        self.assertEqual(metrics(a)['zero_text_intervals'],1)

if __name__=='__main__': unittest.main()
