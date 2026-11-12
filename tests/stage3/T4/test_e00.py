"""Offline contract checks for E00, no network or GPU calls."""
import unittest, copy
from unittest.mock import patch
import run_e00 as r
class E00Tests(unittest.TestCase):
    def setUp(self):
        self.u={'unit_id':'p_u09','required_source_sentences':[{'sentence_index':112},{'sentence_index':113}]}
        self.v={'unit_id':'p_u09','paragraphs':[{'text':'설명한다.','source_indices':[112,113]}],'editorial_candidates':[],'unresolved':[]}
    def bad(self,change):
        v=copy.deepcopy(self.v);change(v)
        with self.assertRaises((ValueError,TypeError)):r.validate(v,self.u)
    def test_valid(self):self.assertEqual(r.validate(self.v,self.u),self.v)
    def test_bool(self):self.bad(lambda v:v['paragraphs'][0].update(source_indices=[True,113]))
    def test_foreign(self):self.bad(lambda v:v['paragraphs'][0].update(source_indices=[112,999]))
    def test_missing(self):self.bad(lambda v:v['paragraphs'][0].update(source_indices=[112]))
    def test_extra(self):self.bad(lambda v:v.update(extra='bad'))
    def test_empty(self):self.bad(lambda v:v['paragraphs'][0].update(text=' '))
    def test_html(self):self.bad(lambda v:v['paragraphs'][0].update(text='<script>bad</script>'))
    def test_duplicate_indices(self):self.bad(lambda v:v['paragraphs'][0].update(source_indices=[112,112,113]))
    def test_wrong_unit(self):self.bad(lambda v:v.update(unit_id='other'))
    def test_duplicate_json(self):
        with self.assertRaises(ValueError):r.prior.loads('{"x":1,"x":2}')
    def test_length(self):
        raw=r.wire({'choices':[{'finish_reason':'length','message':{'content':r.wire(self.v).decode()}}]})
        with self.assertRaises(ValueError):r.parse(raw,200,self.u)
    def test_unknown_budget(self):
        with self.assertRaises(ValueError):r.old.reserve([{'status':'unknown','reserved_seconds':300}],300,27,7200)
    def test_call_budget(self):
        with self.assertRaises(ValueError):r.old.reserve([{'status':'success','http_seconds':1}]*27,300,27,7200)
    def test_time_budget(self):self.assertEqual(r.old.reserve([{'status':'success','http_seconds':7195}],300,27,7200),5)
    def test_source_hash(self):
        with self.assertRaises(ValueError):r.bound_sha(__file__,'0'*64)
    def test_semantic_counterexample(self):
        v=copy.deepcopy(self.v);v['paragraphs'][0]['text']='원문에 없는 거짓 사실이다.'
        self.assertEqual(r.validate(v,self.u),v) # Shape never proves fidelity.
if __name__=='__main__':unittest.main()
