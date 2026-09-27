#!/usr/bin/env python3
"""One bounded acquisition + source projection, never imports or runs a solver.

Labels follow an independently authored documentary adjudication table. This is
coding-agent source adjudication with deterministic expansion, not expert gold.
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, urllib.request, urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/post_confirmation_v13/nih_revision'
SOURCES=DATA/'sources'
SPLITS=('development','validation','final_internal_test')

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def save(p,x):
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x') as f:json.dump(x,f,indent=2,allow_nan=False);f.write('\n')
def stamp(x):return dt.datetime.fromisoformat(x.replace('Z','+00:00'))
def iso(x):return x.isoformat().replace('+00:00','Z')
def canonical(x):return json.dumps(x,separators=(',',':'),sort_keys=True,ensure_ascii=False)

def acquire():
    protocol=read(DATA/'selection_protocol.json')
    for name,wanted in protocol['code_sha256'].items():
        if sha(ROOT/name)!=wanted:raise ValueError('pre-exposure code changed: '+name)
    path=SOURCES/'evidence_21_to_40.json'
    if path.exists():raise FileExistsError('final intake already exists')
    ids=protocol['final_candidate_evidence_ids']
    fields='id fieldName status createdAt updatedAt currentValue suggestedValue acceptanceActivity{id createdAt} rejectionActivity{id createdAt} supersedingActivity{id createdAt}'
    query='query { '+' '.join(f'e{i}:evidenceItem(id:{i})'+'{id name status molecularProfile{id name} disease{id name} source{id citation} revisions(first:100){totalCount pageInfo{hasNextPage} nodes{'+fields+'}}}' for i in ids)+' }'
    payload={'query':query};save(SOURCES/'evidence_21_to_40.request.json',payload)
    url='https://civicdb.org/api/graphql'
    try:
        request=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={'User-Agent':'InvariantCell-bounded-public-research','Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=45) as response:
            raw=response.read(2*1024**2+1);status=response.status
        if len(raw)>2*1024**2:raise ValueError('response exceeded 2 MiB cap')
        path.write_bytes(raw)
        receipt={'url':url,'bytes':len(raw),'sha256':sha(path),'status':status,'observed_at':iso(dt.datetime.now(dt.timezone.utc)),
                 'request_sha256':sha(SOURCES/'evidence_21_to_40.request.json'),'selection_protocol_sha256':sha(DATA/'selection_protocol.json'),
                 'first_public_available_at':None,'observed_snapshot_not_historical_archive':True}
    except urllib.error.HTTPError as error:
        save(SOURCES/'final_intake_failure.json',{'status':error.code,'body_bytes_read':0,'url':url});raise
    save(SOURCES/'final_intake_receipt.json',receipt)
    print(json.dumps({'status':'ACQUIRED_WITHOUT_SOLVER','bytes':len(raw),'sha256':sha(path)}))

def assemble():
    protocol=read(DATA/'selection_protocol.json')
    for name,wanted in protocol['code_sha256'].items():
        if sha(ROOT/name)!=wanted:raise ValueError('pre-exposure code changed: '+name)
    initial=read(SOURCES/'evidence_2_to_20.json')['data']
    final=read(SOURCES/'evidence_21_to_40.json')
    if final.get('errors'):raise ValueError('final API errors; do not use partial data')
    final=final['data']
    exposed=[e for e in initial.values() if e]
    families={e['molecularProfile']['name'].split()[0] for e in exposed}
    publications={e['source']['id'] for e in exposed}
    profiles={e['molecularProfile']['id'] for e in exposed}
    # A conservative observed-public-by bound after both bodies were received.
    observed=iso(dt.datetime.now(dt.timezone.utc));end=iso(stamp(observed)+dt.timedelta(microseconds=1))
    corpora={s:dict(schema_version=13,scope='retrospective_revision_ledger',records=[],queries=[],anchors={},cases=[]) for s in SPLITS}
    labels={s:{} for s in SPLITS};intake=[]
    for source_name,entries in [('evidence_2_to_20.json',initial),('evidence_21_to_40.json',final)]:
        source_path=SOURCES/source_name;source_hash=sha(source_path)
        for key,e in sorted(entries.items(),key=lambda kv:int(kv[0][1:])):
            if e is None:intake.append(dict(evidence_key=key,excluded='missing evidence item'));continue
            family=e['molecularProfile']['name'].split()[0]
            if source_name=='evidence_21_to_40.json':
                if family in families or e['source']['id'] in publications or e['molecularProfile']['id'] in profiles:
                    intake.append(dict(evidence_id=e['id'],excluded='family/publication/profile overlaps exposed initial pool'));continue
                split='final_internal_test'
            else:split='development' if family in protocol['development_families'] else 'validation'
            if e['revisions']['pageInfo']['hasNextPage'] or e['revisions']['totalCount']!=len(e['revisions']['nodes']):
                intake.append(dict(evidence_id=e['id'],excluded='incomplete revision page'));continue
            c=corpora[split]
            for index,r in enumerate(e['revisions']['nodes']):
                rid=f"RID{r['id']}";created=r['createdAt'];status=r['status']
                activity=r.get({'ACCEPTED':'acceptanceActivity','REJECTED':'rejectionActivity','SUPERSEDED':'supersedingActivity','NEW':'noActivity'}.get(status,''))
                resolved=activity['createdAt'] if activity else None
                if status not in {'ACCEPTED','NEW','REJECTED','SUPERSEDED'} or (status!='NEW' and resolved is None):
                    intake.append(dict(revision_id=rid,excluded='unknown disposition or missing resolution event'));continue
                if stamp(created)>=stamp(observed) or (resolved and not stamp(created)<stamp(resolved)<stamp(observed)):
                    intake.append(dict(revision_id=rid,excluded='nonpositive or future curation interval'));continue
                base=dict(subject=rid,context='civic_editorial_workflow',source_sha256=source_hash,
                          public_available_at=observed,ingested_at=observed,synthetic=False,state_kind='observed',validity_kind='interval')
                pointer=f'/data/{key}/revisions/nodes/{index}'
                anchor=dict(source_path=str(source_path.relative_to(ROOT)),source_sha256=source_hash,json_pointer=pointer,
                            evidence_item_id=e['id'],field_name=r['fieldName'],stored_prior_value=r['currentValue'],
                            proposed_value=r['suggestedValue'],revision_status=status,created_at=created,resolution_at=resolved,
                            context_metadata_time_basis='current snapshot; historical disease/molecular scope unknown',
                            source_publication_id=e['source']['id'],molecular_profile=e['molecularProfile'],disease=e['disease'],
                            source_link=f'https://civicdb.org/links/revision/{r["id"]}',biological_event_time=None,
                            first_public_available_at=None,observed_public_by=observed)
                pending_id=rid+':pending';terminal_id=rid+':terminal';replacement_id=rid+':replacement'
                def record(eid,predicate,value,polarity,start,stop,event):
                    c['records'].append(dict(**base,evidence_id=eid,predicate=predicate,value=value,polarity=polarity,valid_from=start,valid_to=stop,event_time=event))
                    c['anchors'][eid]=anchor
                record(pending_id,'editorially_accepted_revision',rid,'refutes',created,resolved or end,created)
                if resolved:
                    record(terminal_id,'editorially_accepted_revision',rid,'supports' if status=='ACCEPTED' else 'refutes',resolved,end,resolved)
                if status=='ACCEPTED':
                    record(replacement_id,'accepted_replacement_value',canonical(r['suggestedValue']),'supports',resolved,end,resolved)
                c['cases'].append(dict(id=rid,evidence_item_id=e['id'],family=family,publication_id=e['source']['id'],
                                       field_name=r['fieldName'],revision_id=r['id'],split=split,
                                       source_pointer=pointer,source_path=str(source_path.relative_to(ROOT))))
                def query(suffix,valid,label,support,predicate='editorially_accepted_revision',known=None,event=None):
                    qid=rid+':q'+str(sum(q['case_id']==rid for q in c['queries'])+1).zfill(2)
                    q=dict(subject=rid,predicate=predicate,context='civic_editorial_workflow',valid_at=valid,
                           evidence_cutoff=known or observed,ingestion_cutoff=observed,event_cutoff=event)
                    c['queries'].append(dict(id=qid,case_id=rid,split=split,query=q))
                    labels[split][qid]=dict(outcome=label,support_ids=support,adjudication='coding-agent source adjudication; deterministic query expansion; not clinical-expert gold',
                        source_sha256=source_hash,source_pointer=pointer,rule=suffix,
                        rationale='CIViC revision creation/resolution fields and stored replacement; current observation supplies only availability upper bound.')
                before=iso(stamp(created)-dt.timedelta(seconds=1))
                middle=iso(stamp(created)+(stamp(resolved or observed)-stamp(created))/2)
                query('before_proposal',before,'ABSTAIN',[])
                query('pending',middle,'NOT_ACCEPTED',[pending_id])
                query('observed_disposition',observed,'ACCEPTED' if status=='ACCEPTED' else 'NOT_ACCEPTED',[terminal_id if resolved else pending_id])
                query('historical_availability_unknown',middle,'ABSTAIN',[],known=created)
                if resolved:
                    query('resolution_boundary',resolved,'ACCEPTED' if status=='ACCEPTED' else 'NOT_ACCEPTED',[terminal_id])
                    query('curation_event_cutoff',resolved,'ABSTAIN',[],event=created)
                if status=='ACCEPTED':
                    query('replacement_not_yet_accepted',middle,'ABSTAIN',[],predicate='accepted_replacement_value')
                    query('documented_accepted_replacement',resolved,'VALUE:'+canonical(r['suggestedValue']),[replacement_id],predicate='accepted_replacement_value')
            intake.append(dict(evidence_id=e['id'],split=split,revision_count=e['revisions']['totalCount']))
    for split in SPLITS:
        save(DATA/split/'corpus.json',corpora[split])
        save(DATA/'evaluator_only'/f'{split}_gold.json',labels[split])
    save(DATA/'evaluator_only/intake_adjudication.json',{'observed_public_by':observed,'decisions':intake,
          'final_bodies_seen_by_source_adjudicating_agent':True,'solver_outputs_examined':False,
          'clinical_expert_review':False,'clinical_current_field_reconstruction':'NOT_SUPPORTED_WITHOUT_CONTIGUOUS_COMPLETE_HISTORY'})
    print(json.dumps({'status':'SOURCE_PROJECTION_AND_SEPARATE_LABELS_SAVED','solver_run':False,
                      'counts':{s:{'cases':len(corpora[s]['cases']),'queries':len(corpora[s]['queries'])} for s in SPLITS}}))

if __name__=='__main__':
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--acquire-final',action='store_true');g.add_argument('--assemble',action='store_true');a=p.parse_args()
    acquire() if a.acquire_final else assemble()
