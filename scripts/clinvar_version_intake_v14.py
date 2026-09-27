#!/usr/bin/env python3
"""Freeze/check already acquired ClinVar metadata; no downloads or reasoning."""
import argparse,datetime,hashlib,json
from pathlib import Path
from invariantcell.clinvar_version_v14 import inspect_version
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data/post_confirmation_v14/nih_clinvar'

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def save(p,x):
    with Path(p).open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def metadata():
    protocol=read(DATA/'intake_protocol.json');rows=[]
    for acc in protocol['accessions']:
        for version in protocol['versions']:
            requested=f'{acc}.{version}';p=DATA/'sources'/f'{requested}.xml'
            rows.append({**inspect_version(p.read_bytes(),requested),'source_path':str(p.relative_to(ROOT)),'source_sha256':sha(p)})
    return rows

def main():
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--prepare',action='store_true');g.add_argument('--check',action='store_true');a=p.parse_args()
    if a.check:
        freeze=read(DATA/'source_family_freeze.json')
        for name,digest in freeze['files'].items():
            path=ROOT/name
            if path.is_symlink() or not path.resolve().is_relative_to(ROOT) or sha(path)!=digest:raise ValueError('source-family freeze mismatch: '+name)
        if metadata()!=read(DATA/'qualified_metadata.json'):raise ValueError('metadata projection differs')
        print(json.dumps({'status':'SOURCE_FAMILY_FREEZE_VALID','qualified_version_records':7,'rejected_version_responses':2,'qualified_multi_version_series':2,'gold_frozen':False,'reasoner_run':False}));return
    rows=metadata();save(DATA/'qualified_metadata.json',rows)
    series={}
    for row in rows:
        if row['status']=='QUALIFIED_VERSION_METADATA':series.setdefault(row['requested'].split('.')[0],[]).append(row['requested'])
    admitted={k:v for k,v in series.items() if len(v)>=2}
    save(DATA/'case_universe.json',{'status':'METADATA_SOURCE_UNIVERSE_FROZEN_GOLD_PENDING','series':admitted,
        'singleton_not_admitted_to_history_benchmark':[v for v in series.values() if len(v)<2],
        'rejected_responses':[r['requested'] for r in rows if r['status']!='QUALIFIED_VERSION_METADATA'],
        'candidate_adjacent_version_pairs':[[versions[i],versions[i+1]] for versions in admitted.values() for i in range(len(versions)-1)],
        'gold_labels':None,'classification_values_not_yet_adjudicated':True,'model_policy_access':'No new source-family solver/evaluation until independent source-field labels and procedure/protocol are frozen.',
        'first_public_available_at':None,'unsupported_categories':['ontology version mismatch not established; a transcript accession version is not an ontology version','clinical correctness not established','actual historical release times not established'],
        'separation':'Keep this family separate from the consumed CIViC dataset. It is not a real next-measurement endpoint.'})
    files=[p for p in DATA.rglob('*') if p.is_file()]+[ROOT/'src/invariantcell/clinvar_version_v14.py',ROOT/'scripts/clinvar_version_intake_v14.py',ROOT/'tests/test_clinvar_version_v14.py']
    save(DATA/'source_family_freeze.json',{'schema_version':14,'frozen_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'QUALIFIED_SOURCE_METADATA_ONLY_GOLD_PENDING','files':{str(p.relative_to(ROOT)):sha(p) for p in files},'classification_values_inspected':False,'reasoner_or_policy_outputs_examined':False,'clinical_expert_gold':False,'prize_eligibility_established':False})
    print(json.dumps({'status':'SOURCE_FAMILY_METADATA_FROZEN','qualified_version_records':sum(r['status']=='QUALIFIED_VERSION_METADATA' for r in rows),'rejected_responses':sum(r['status']!='QUALIFIED_VERSION_METADATA' for r in rows),'multi_version_series':len(admitted),'gold_frozen':False}))
if __name__=='__main__':main()
