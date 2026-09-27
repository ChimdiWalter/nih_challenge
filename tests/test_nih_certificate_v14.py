"""Synthetic proof/tamper tests; no consumed or new-source answers generated."""
import copy,unittest,json,tempfile,importlib.util
from pathlib import Path
from unittest.mock import patch
from invariantcell.core import canonical_hash
from invariantcell.temporal import EvidenceRecord,Snapshot,TemporalQuery,issue_certificate
from invariantcell.replay import verify_certificate
from invariantcell.nih_certificate_v14 import audit_query,tamper,TAMPER_KINDS,contradiction_witnesses

class CertificateAuditTests(unittest.TestCase):
    def setUp(self):
        self.r=dict(evidence_id='R:1',subject='RID1',predicate='editorially_accepted_revision',value='RID1',context='test',polarity='supports',public_available_at='2020-01-01T00:00:00Z',ingested_at='2020-01-01T00:00:00Z',source_sha256='a'*64,validity_kind='interval',valid_from='2020-01-01T00:00:00Z',valid_to=None,event_time='2020-01-01T00:00:00Z')
        self.q=dict(subject='RID1',predicate='editorially_accepted_revision',context='test',evidence_cutoff='2021-01-01T00:00:00Z',ingestion_cutoff='2021-01-01T00:00:00Z',valid_at='2021-01-01T00:00:00Z')
        self.snapshot=Snapshot('v13:Q1',(EvidenceRecord(**self.r),));self.query=TemporalQuery(**self.q);self.c=issue_certificate(self.snapshot,self.query)
        self.anchor={'R:1':{'source_path':'toy.json','json_pointer':'/r','first_public_available_at':None}}

    def test_saved_audit_never_calls_answer_generator(self):
        p={'certificate':self.c,'used_ids':['R:1'],'outcome':'ACCEPTED'}
        with patch('invariantcell.temporal.evaluate',side_effect=AssertionError('answer rerun prohibited')),patch('invariantcell.temporal.issue_certificate',side_effect=AssertionError('issuer prohibited')):
            out=audit_query([self.r],{'id':'Q1','case_id':'RID1','query':self.q},p,{'outcome':'ACCEPTED'},self.anchor,{'R:1':True})
        self.assertTrue(out['replay_result']['valid']);self.assertTrue(out['replay_reproducible']);self.assertTrue(out['provenance_complete'])
        self.assertEqual(out['future_publication_leakage']['count'],None)

    def test_rehashed_tampers_fail_external_trust_anchors(self):
        for kind in TAMPER_KINDS:
            result=verify_certificate(tamper(self.c,kind),self.snapshot,canonical_hash(self.snapshot.to_dict()),self.query)
            self.assertFalse(result.valid,kind)

    def test_independent_synthetic_contradiction_witness(self):
        opposing=dict(self.r,evidence_id='R:2',polarity='refutes')
        snap=Snapshot('conflict',(EvidenceRecord(**self.r),EvidenceRecord(**opposing)))
        c=issue_certificate(snap,self.query);w=contradiction_witnesses(c)
        self.assertEqual(w,[{'value':'RID1','support_ids':['R:1'],'refute_ids':['R:2']}])
        self.assertTrue(verify_certificate(c,snap,canonical_hash(snap.to_dict()),self.query).valid)

    def test_unknown_availability_has_typed_abstention(self):
        r=dict(self.r,public_available_at=None);snap=Snapshot('v13:Q1',(EvidenceRecord(**r),));c=issue_certificate(snap,self.query)
        out=audit_query([r],{'id':'Q1','case_id':'RID1','query':self.q},{'certificate':c,'used_ids':[],'outcome':'ABSTAIN'},{'outcome':'ABSTAIN'},self.anchor,{'R:1':True})
        self.assertEqual(out['typed_abstention_reasons'],['HISTORICAL_PUBLIC_AVAILABILITY_NOT_ESTABLISHED'])
        self.assertTrue(out['answer_correct_against_consumed_source_gold'])

    def test_saved_answer_must_match_certificate(self):
        out=audit_query([self.r],{'id':'Q1','case_id':'RID1','query':self.q},{'certificate':self.c,'used_ids':['R:1'],'outcome':'NOT_ACCEPTED'},{'outcome':'NOT_ACCEPTED'},self.anchor,{'R:1':True})
        self.assertFalse(out['saved_answer_bound_to_certificate'])

    def test_source_dates_checked_against_primary_json_pointer(self):
        spec=importlib.util.spec_from_file_location('v14_audit_script',Path(__file__).resolve().parents[1]/'scripts/nih_certificate_audit_v14.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'toy.json'
            node=dict(id=1,currentValue='old',suggestedValue='new',fieldName='description',createdAt='2020-01-01T00:00:00Z',status='ACCEPTED',acceptanceActivity={'createdAt':'2020-01-02T00:00:00Z'})
            source.write_text(json.dumps({'r':node}));digest=module.sha(source)
            a=dict(source_path='toy.json',json_pointer='/r',source_sha256=digest,stored_prior_value='old',proposed_value='new',field_name='description',created_at=node['createdAt'],revision_status='ACCEPTED',resolution_at='2020-01-02T00:00:00Z',observed_public_by='2021-01-01T00:00:00Z')
            r=dict(self.r,evidence_id='RID1:pending',source_sha256=digest,polarity='refutes',valid_to='2020-01-02T00:00:00Z',public_available_at=a['observed_public_by'],ingested_at=a['observed_public_by'])
            with patch.object(module,'ROOT',root):
                self.assertTrue(module.source_anchor_checks([r],{r['evidence_id']:a})[r['evidence_id']])
                r['event_time']='2020-01-03T00:00:00Z'
                self.assertFalse(module.source_anchor_checks([r],{r['evidence_id']:a})[r['evidence_id']])

    def test_missing_provenance_is_not_vacuous_pass(self):
        out=audit_query([self.r],{'id':'Q1','case_id':'RID1','query':self.q},{'certificate':self.c,'used_ids':['R:1'],'outcome':'ACCEPTED'},{'outcome':'ACCEPTED'},self.anchor,{})
        self.assertFalse(out['provenance_complete'])

if __name__=='__main__':unittest.main()
