#!/usr/bin/env python3
"""Root-queued audit of saved, already consumed v13 final certificates."""
import os
for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):os.environ[k]='1'
import argparse,json,resource,signal,time,datetime
from pathlib import Path
from invariantcell.nih_certificate_v14 import audit_query,summary,sha
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/post_confirmation_v14/nih_certificate'
OLD=ROOT/'data/post_confirmation_v13/nih_revision'
RUN=ROOT/'runs/post_confirmation_v13/nih-civic-revisions-v1'

def read(p):return json.loads(Path(p).read_text())
def save(p,x):
    with Path(p).open('x') as f:json.dump(x,f,indent=2,allow_nan=False);f.write('\n')
def load_protocol():
    p=read(DATA/'protocol.json')
    for name,digest in p['files'].items():
        path=ROOT/name
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT) or sha(path)!=digest:raise ValueError('frozen audit input mismatch: '+name)
    return p

def source_anchor_checks(records,anchors):
    cache={};result={}
    for r in records:
        a=anchors[r['evidence_id']];p=ROOT/a['source_path']
        if p.is_symlink() or not p.resolve().is_relative_to(ROOT):raise ValueError('source boundary')
        if str(p) not in cache:cache[str(p)]=(sha(p),read(p))
        digest,node=cache[str(p)]
        for part in a['json_pointer'].lstrip('/').split('/'):
            key=part.replace('~1','/').replace('~0','~');node=node[int(key)] if isinstance(node,list) else node[key]
        activity=node.get({'ACCEPTED':'acceptanceActivity','REJECTED':'rejectionActivity','SUPERSEDED':'supersedingActivity'}.get(node['status'],''))
        resolved=activity['createdAt'] if activity else None
        role=r['evidence_id'].rsplit(':',1)[-1]
        expected_start=node['createdAt'] if role=='pending' else resolved
        expected_polarity='supports' if role=='replacement' or (role=='terminal' and node['status']=='ACCEPTED') else 'refutes'
        observed=datetime.datetime.fromisoformat(a['observed_public_by'].replace('Z','+00:00'))
        expected_end=resolved if role=='pending' and resolved else (observed+datetime.timedelta(microseconds=1)).isoformat().replace('+00:00','Z')
        result[r['evidence_id']]=(digest==r['source_sha256']==a['source_sha256'] and r['subject']=='RID'+str(node['id'])
              and a['stored_prior_value']==node['currentValue'] and a['proposed_value']==node['suggestedValue']
              and a['field_name']==node['fieldName'] and a['created_at']==node['createdAt'] and a['revision_status']==node['status']
              and a['resolution_at']==resolved and r['valid_from']==expected_start and r['event_time']==expected_start
              and r['valid_to']==expected_end and r['polarity']==expected_polarity
              and r['public_available_at']==r['ingested_at']==a['observed_public_by']
              and role in {'pending','terminal','replacement'})
    return result

def main():
    p=argparse.ArgumentParser();a=p.add_mutually_exclusive_group(required=True);a.add_argument('--check',action='store_true');a.add_argument('--run',action='store_true');p.add_argument('--output',type=Path);args=p.parse_args()
    signal.alarm(180);resource.setrlimit(resource.RLIMIT_CPU,(180,180));resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
    protocol=load_protocol()
    if args.check:print(json.dumps({'status':'AUDIT_INPUT_HASHES_VALID','expected_final_queries':138,'answer_generation':False,'independent_replay_run':False}));return
    if args.output is None or not args.output.resolve().is_relative_to(ROOT/'runs/post_confirmation_v14'):p.error('new v14 output required')
    args.output.mkdir(parents=True,exist_ok=False);started=time.monotonic()
    save(DATA/'audit_attempt.json',{'protocol_sha256':sha(DATA/'protocol.json'),'output':str(args.output)})
    try:
        corpora=[read(OLD/s/'corpus.json') for s in ('development','validation','final_internal_test')]
        records=[r for c in corpora for r in c['records']];anchors={k:v for c in corpora for k,v in c['anchors'].items()}
        query_rows=corpora[-1]['queries'];gold=read(OLD/'evaluator_only/final_internal_test_gold.json')
        predictions=read(RUN/'predictions.json')['verified_temporal'];old_results=read(RUN/'results.json')
        if len(query_rows)!=138 or {q['id'] for q in query_rows}!=set(gold):raise ValueError('consumed final-panel identity mismatch')
        checked=source_anchor_checks(records,anchors)
        if not all(checked.values()):raise ValueError('source anchor content mismatch')
        rows=[audit_query(records,q,predictions[q['id']],gold[q['id']],anchors,checked) for q in query_rows]
        save(args.output/'certificate_exports.json',rows)
        metrics=summary(rows);expected=next(m['correct'] for m in old_results['metrics'] if m['method']=='verified_temporal' and m['split']=='final_internal_test')
        metrics['matches_archived_correctness_total']=metrics['answer_correct']==expected
        save(args.output/'results.json',{'status':'COMPLETE','scope':'POSTHOC_CERTIFICATE_AUDIT_CONSUMED_PANEL_NOT_NEW_ANSWERS',
          'protocol_sha256':sha(DATA/'protocol.json'),'source_predictions_sha256':old_results['predictions_sha256'],
          'certificate_exports_sha256':sha(args.output/'certificate_exports.json'),'metrics':metrics,
          'wall_seconds':time.monotonic()-started,'maxrss_KiB':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
          'original_answer_generator_called':False,'original_benchmark_rerun':False,'prize_eligibility_established':False})
    except BaseException as e:
        save(args.output/'failure.json',{'status':'FAILED_ATTEMPT_CONSUMED','error':repr(e),'wall_seconds':time.monotonic()-started,'maxrss_KiB':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss});raise
    print(args.output/'results.json')
if __name__=='__main__':main()
