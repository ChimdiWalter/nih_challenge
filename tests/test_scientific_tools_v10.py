import copy,unittest
from unittest.mock import patch
from invariantcell.io import digest
from invariantcell.scientific_tools_v10 import ToolCall,candidate,verify,run_workflow,explain_decision_change
class ScientificToolsTests(unittest.TestCase):
    def setUp(self):
        self.records={k:{'record_id':k,'kind':'synthetic','source_sha256':'a'*64,'unit':'count','public_available_at':'2020-01-01T00:00:00Z'} for k in ['a','b']}
        self.call=ToolCall('compare_field',('a','b'),'unit')
    def test_independent_replay_does_not_call_producer(self):
        c=candidate(self.call,self.records)
        with patch('invariantcell.scientific_tools_v10.candidate',side_effect=AssertionError()):
            self.assertTrue(verify(c,self.call,self.records,{k:digest(v) for k,v in self.records.items()})['valid'])
    def test_rehashed_false_conclusion_rejected(self):
        c=candidate(self.call,self.records);c['outcome']='MISMATCH';c['certificate_hash']=digest({k:v for k,v in c.items() if k!='certificate_hash'})
        with self.assertRaises(ValueError):verify(c,self.call,self.records,{k:digest(v) for k,v in self.records.items()})
    def test_wrong_external_anchors_and_query_rejected(self):
        c=candidate(self.call,self.records)
        with self.assertRaises(ValueError):verify(c,self.call,self.records,{})
        with self.assertRaises(ValueError):verify(c,ToolCall('compare_field',('a','b'),'quantity'),self.records,{k:digest(v) for k,v in self.records.items()})
    def test_unknown_is_not_matching(self):
        self.records['b']['unit']=None;self.assertEqual(run_workflow([self.call],self.records)['decision'],'INSUFFICIENT_EVIDENCE')
    def test_future_and_endpoint_reuse(self):
        self.assertEqual(candidate(ToolCall('available_as_of',('a',),cutoff='2019-01-01T00:00:00Z'),self.records)['outcome'],'FUTURE')
        self.records['a'].update(used_for=['model_selection'],lineage_complete=True)
        self.assertEqual(candidate(ToolCall('independent_endpoint',('a',)),self.records)['outcome'],'NOT_INDEPENDENT')
    def test_arbitrary_code_fields_and_budget_refused(self):
        for tool,field in [('exec',None),('compare_field','__dict__')]:
            with self.assertRaises(ValueError):ToolCall(tool,('a','b'),field)
        with self.assertRaises(ValueError):run_workflow([self.call]*13,self.records)
    def test_actual_revision_explanation_does_not_invent_progression(self):
        before=run_workflow([self.call],self.records);new=copy.deepcopy(self.records);new['b']['unit']='area'
        after=run_workflow([self.call],new);d=explain_decision_change(before,after,self.records,new,'annotation_revision')
        self.assertEqual(d['changed_evidence'][0]['record_id'],'b');self.assertEqual(d['unchanged_records'],['a']);self.assertFalse(d['biological_progression_inferred'])
    def test_change_explanation_rejects_unbound_records(self):
        before=run_workflow([self.call],self.records);different=copy.deepcopy(self.records);different['b']['unit']='area'
        with self.assertRaises(ValueError):explain_decision_change(before,before,different,self.records,'annotation_revision')
    def test_json_roundtrip_replays(self):
        import json
        c=json.loads(json.dumps(candidate(self.call,self.records)))
        self.assertTrue(verify(c,self.call,self.records,{k:digest(v) for k,v in self.records.items()})['valid'])
    def test_known_reuse_blocks_even_incomplete_lineage(self):
        self.records['a'].update(used_for=['model_selection'],lineage_complete=False)
        call=ToolCall('independent_endpoint',('a',));c=candidate(call,self.records)
        self.assertEqual(c['outcome'],'NOT_INDEPENDENT')
        self.assertTrue(verify(c,call,self.records,{'a':digest(self.records['a'])})['valid'])
    def test_rehashed_overclaimed_scope_refused(self):
        c=candidate(self.call,self.records);c['scope']='Biological truth'
        c['certificate_hash']=digest({k:v for k,v in c.items() if k!='certificate_hash'})
        with self.assertRaises(ValueError):verify(c,self.call,self.records,{k:digest(v) for k,v in self.records.items()})
