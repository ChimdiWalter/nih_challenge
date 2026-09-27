"""Metadata-frozen NuCLS patient groups; intake is distinct from queued scoring."""
from __future__ import annotations
import os
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[key]='1'
os.environ['CUDA_VISIBLE_DEVICES']=''
from pathlib import Path
import argparse,csv,hashlib,json,re,resource,signal,sys,time,urllib.parse,urllib.request
from datetime import datetime,timezone
import numpy as np
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'src'))
from invariantcell.pathology_v10 import RECIPE,predict_instances,read_annotations,references,instance_boxes,match_objects,score_measurements
from invariantcell.io import file_hash,utc_now
D=P/'data/pathology/v13_nucls';OUT=P/'runs/post_confirmation_v13/pathology-nucls-six-fovs-v1'
OLD=P/'data/post_confirmation_v10/pathology'
BASELINE_SHA='329b9165d044e16778395cd69f6dd209fd9a9e8545b37385a1929d01e1ef55c4'
BARCODE='https://docs.gdc.cancer.gov/Encyclopedia/pages/TCGA_Barcode/'
LIMIT=100*1024**2


def read(p):return json.loads(Path(p).read_text())
def write(p,obj):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x')as f:json.dump(obj,f,indent=2,allow_nan=False);f.write('\n')


def fetch(url,path,cap=4*1024**2):
    """Unauthenticated public GET; immutable success/failure receipt, no retry."""
    path=Path(path);receipt=Path(str(path)+'.download.json')
    if path.exists():
        r=read(receipt)
        if r['sha256']!=file_hash(path) or r['url']!=url:raise ValueError('Existing download changed')
        return r
    if Path(str(path)+'.failure.json').exists():raise ValueError('Earlier intake failed; explicit review required, no automatic retry')
    used=sum(read(p).get('bytes',0)for p in D.rglob('*.download.json')) if D.exists() else 0
    if used+cap>LIMIT:raise ValueError('100 MiB download reservation exceeded')
    path.parent.mkdir(parents=True,exist_ok=True);temp=Path(str(path)+'.partial')
    start=time.monotonic();n=0
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'InvariantCell-public-metadata-intake/13'}),timeout=30)as response,temp.open('xb')as out:
            while True:
                chunk=response.read(min(65536,cap+1-n))
                if not chunk:break
                n+=len(chunk)
                if n>cap:raise ValueError('Download exceeded file cap')
                out.write(chunk)
            status=response.status;content_type=response.headers.get('Content-Type');final_url=response.url
        temp.rename(path)
        r={'url':url,'final_url':final_url,'http_status':status,'content_type':content_type,'bytes':n,
           'sha256':file_hash(path),'retrieved_at':utc_now(),'wall_seconds':time.monotonic()-start}
        write(receipt,r);return r
    except BaseException as e:
        write(Path(str(path)+'.failure.json'),{'url':url,'bytes':n,'error':repr(e),'automatic_retry':False});raise


def patient(stem):
    m=re.match(r'^(TCGA-[A-Z0-9]{2}-[A-Z0-9]{4})-(DX\d+)_id-[a-z0-9]+_left-\d+_top-\d+_bottom-\d+_right-\d+$',stem)
    if not m:raise ValueError('Unrecognized NuCLS participant/slide filename')
    return m[1],m[1]+'-'+m[2]


def listing(path):
    from bs4 import BeautifulSoup
    items={}
    for row in BeautifulSoup(Path(path).read_text(),'html.parser').select('tr[data-id]'):
        node=row.select_one('strong')
        if node:
            name=node.get_text();stem=Path(name).stem
            if stem in items:raise ValueError('Duplicate listing stem')
            items[stem]={'download_id':row['data-id'],'filename':name}
    return items


def select_cases(listings,excluded,per_patient=2,patients=3):
    common=set.intersection(*(set(x)for x in listings.values()));groups={}
    for stem in sorted(common):
        pid,slide=patient(stem)
        if pid not in excluded:groups.setdefault(pid,[]).append((stem,slide))
    if len(groups)<patients:raise ValueError('Insufficient disjoint patient groups')
    result=[]
    for pid in sorted(groups)[:patients]:
        if len(groups[pid])<per_patient:raise ValueError('Insufficient FOV metadata')
        for stem,slide in groups[pid][:per_patient]:
            result.append({'patient_id':pid,'slide_id':slide,'fov_id':stem,
                           'assets':{role:rows[stem]for role,rows in listings.items()}})
    return result


def freeze():
    if (D/'selection.json').exists():raise ValueError('Selection already frozen; do not replace')
    old=read(OLD/'package.json');acquisition=read(OLD/'acquisition_metadata.json')
    names={'image':'rgb','annotations':'csv','mask':'mask'};listings={};pins={}
    for role,name in names.items():
        path=OLD/f'nucls_{name}_folder.txt';expected=next(x['sha256']for x in acquisition if x.get('path')==str(path.relative_to(P)))
        if file_hash(path)!=expected:raise ValueError('Archived metadata changed')
        listings[role]=listing(path);pins[str(path.relative_to(P))]=expected
    cases=select_cases(listings,{old['patient_id']});ids=sorted({x['patient_id']for x in cases})
    # Identity metadata only: no clinical fields, annotations or image pixels.
    query={'op':'in','content':{'field':'submitter_id','value':ids}}
    url='https://api.gdc.cancer.gov/cases?'+urllib.parse.urlencode({'filters':json.dumps(query),'fields':'case_id,submitter_id,project.project_id','size':'10','format':'JSON'})
    fetch(url,D/'metadata/gdc_cases.json',1024**2);gdc=read(D/'metadata/gdc_cases.json')
    hits=gdc['data']['hits'];crosswalk={x['submitter_id']:x['case_id']for x in hits}
    if set(crosswalk)!=set(ids) or len(set(crosswalk.values()))!=len(ids):raise ValueError('GDC patient identity unresolved')
    if any(x['project']['project_id']!='TCGA-BRCA'for x in hits):raise ValueError('Unexpected GDC project')
    fetch(BARCODE,D/'metadata/gdc_barcode.html',1024**2)
    fetch('https://drive.google.com/uc?export=download&id=1imGDC_26sTgIoCCbsan10VwbTTDSXf2x',D/'metadata/nucls_slide_names.txt',1024**2)
    slide_names=(D/'metadata/nucls_slide_names.txt').read_text()
    for c in cases:
        if c['slide_id']not in slide_names:raise ValueError('Selected slide absent from authors slide list')
        c['gdc_case_uuid']=crosswalk[c['patient_id']]
    for path in (D/'metadata').iterdir():
        if path.is_file():pins[str(path.relative_to(P))]=file_hash(path)
    for name in ['nucls_format.txt','nucls_readme.txt','nucls_single_rater.txt','package.json']:
        pins[str((OLD/name).relative_to(P))]=file_hash(OLD/name)
    write(D/'selection.json',{'schema':'nucls-v13-metadata-freeze-1','status':'FROZEN_BEFORE_NEW_ANNOTATION_OR_IMAGE_CONTENT',
        'frozen_at':utc_now(),'cases':cases,'excluded_participants':[old['patient_id']],
        'rule':'First 3 lexicographic participants excluding old demonstration; first 2 common FOV stems each in archived first-page listings. No substitution after intake failure.',
        'grouping':'NuCLS filename participant barcode, authors slide list, and public GDC case UUID crosswalk; no P09 molecular/specimen join.',
        'patients':3,'fovs':6,'reference_values_opened':False,'new_image_pixels_opened':False,'input_sha256':pins,
        'baseline_sha256':BASELINE_SHA,'baseline_recipe':RECIPE,'pixel_size_um':[.2,.2],
        'rights':{'data_license':'CC0-1.0','source':'https://sites.google.com/view/nucls/single-rater','prize_or_institutional_rights_established':False},
        'outcome_policy':'All six references withheld until fixed predictions for all dimension-valid FOVs are committed; no tuning or replacements; no clinical/general-population claim.',
        'history_policy':'Content-addressed corrected annotation version; specimen, annotation and first-public-availability times unknown. Dataset correction workflow is not a per-FOV dated predecessor.'})
    print(D/'selection.json')


def verify_selection():
    selection=read(D/'selection.json')
    if file_hash(P/'src/invariantcell/pathology_v10.py')!=BASELINE_SHA or selection['baseline_recipe']!=RECIPE:raise ValueError('Fixed baseline changed')
    for name,expected in selection['input_sha256'].items():
        if file_hash(P/name)!=expected:raise ValueError('Selection metadata changed')
    if selection['reference_values_opened'] is not False or len(selection['cases'])!=6:raise ValueError('Invalid selection freeze')
    pids={c['patient_id']for c in selection['cases']}
    if len(pids)!=3 or pids&set(selection['excluded_participants']):raise ValueError('Patient overlap')
    return selection


def intake():
    from PIL import Image
    selection=verify_selection();cases=[]
    for case in selection['cases']:
        records={}
        for role,asset in case['assets'].items():
            extension='.csv' if role=='annotations' else '.png'
            path=D/'assets'/case['fov_id']/(role+extension)
            receipt=fetch('https://drive.google.com/uc?export=download&id='+asset['download_id'],path)
            records[role]={'path':str(path.relative_to(P)),**receipt}
        with Image.open(P/records['image']['path'])as im:rgb_shape=im.size;rgb_mode=im.mode
        with Image.open(P/records['mask']['path'])as im:mask_shape=im.size;mask_mode=im.mode
        valid=rgb_shape==mask_shape and rgb_mode==mask_mode=='RGB'
        cases.append({**case,'assets':records,'image_size_xy':list(rgb_shape),'mask_size_xy':list(mask_shape),
            'status':'READY_REFERENCE_CONTENT_UNOPENED'if valid else'INVALID_DIMENSIONS_OR_MODE_NO_REPLACEMENT',
            'annotation_version':'NuCLS-QC-sha256:'+records['annotations']['sha256'],
            'annotation_history':{'stage':'corrected_single_rater','observed_at':None,'annotated_at':None,'public_available_at':None,
                'retrieved_at':records['annotations']['retrieved_at'],'predecessor_version':None,'historical_replay':'ABSTAIN_PUBLIC_AVAILABILITY_UNKNOWN'}})
    write(D/'intake.json',{'status':'INTAKE_COMPLETE_NO_SCORING','selection_sha256':file_hash(D/'selection.json'),'cases':cases,
        'annotation_contents_parsed':False,'image_pixels_used':False,'header_checks_only':True,
        'bytes_downloaded':sum(read(p).get('bytes',0)for p in D.rglob('*.download.json')),'at':utc_now()})
    print(D/'intake.json')


def moments(binary,scale):
    yy,xx=np.where(binary);n=len(xx)
    if n<2:return None
    coords=np.column_stack([xx*scale[0],yy*scale[1]]);centered=coords-coords.mean(0)
    values=np.maximum(np.linalg.eigvalsh(centered.T@centered/n),0)
    return {'area_um2':float(n*np.prod(scale)),'major_axis_um':float(4*np.sqrt(values[1])),
        'minor_axis_um':float(4*np.sqrt(values[0])),'eccentricity':float(np.sqrt(1-values[0]/values[1]))if values[1]>0 else 0.}


def morphology(labels,rows,mask,scale):
    from PIL import Image,ImageDraw
    _,valid,roi,objects=references(rows,mask);boxes,ids=instance_boxes(labels,roi)
    ref=[r for r in objects if roi[(r['box'][1]+r['box'][3])//2,(r['box'][0]+r['box'][2])//2]]
    matching=match_objects(boxes,[r['box']for r in ref],RECIPE['box_iou_threshold']);pairs=[];excluded=0
    for match in matching['matches']:
        row=ref[match['reference_index']]
        if row['type']!='polyline':excluded+=1;continue
        canvas=Image.new('1',(labels.shape[1],labels.shape[0]));ImageDraw.Draw(canvas).polygon(row['vertices'],fill=1)
        truth=np.asarray(canvas,dtype=bool);pred=labels==ids[match['prediction_index']]
        border=lambda a:bool(a[0].any()or a[-1].any()or a[:,0].any()or a[:,-1].any())
        if (truth&~valid).any()or(pred&~valid).any()or border(truth)or border(pred):excluded+=1;continue
        a=moments(pred,scale);b=moments(truth,scale)
        if a is None or b is None:excluded+=1;continue
        pairs.append({'source_row':row['source_row'],'predicted':a,'reference':b,'absolute_errors':{k:abs(a[k]-b[k])for k in a}})
    return {'status':'COMPLETE'if pairs else'UNDEFINED_NO_FULLY_VALID_MATCHED_POLYLINES','eligible_pairs':len(pairs),
        'excluded_matches':excluded,'pairs':pairs,'mean_absolute_errors':{k:float(np.mean([r['absolute_errors'][k]for r in pairs]))for k in pairs[0]['absolute_errors']}if pairs else None,
        'scope':'Matched full polyline objects only; rectangles, border-truncated and pixel-ineligible instances excluded. Selection-conditioned shape diagnostic, not all-object accuracy.'}


def aggregate(results):
    """Equal participant weights; missing FOVs/patients are explicit, never zero."""
    groups={}
    for r in results:groups.setdefault(r['patient_id'],[]).append(r)
    patients=[]
    for pid,rows in sorted(groups.items()):
        good=[x for x in rows if x['status']=='SCORED']
        scores={}
        for key,get in [('object_f1',lambda m:m['objects']['matching']['f1']),('pixel_dice',lambda m:m['pixel']['dice']),
                       ('count_absolute_error',lambda m:m['objects']['absolute_count_error']),('area_absolute_error_um2',lambda m:m['area']['absolute_error_um2'])]:
            values=[get(x['metrics'])for x in good];values=[v for v in values if v is not None]
            scores[key]=float(np.mean(values))if values else None
        for key in ['area_um2','major_axis_um','minor_axis_um','eccentricity']:
            values=[x['morphology']['mean_absolute_errors'][key]for x in good if x.get('morphology',{}).get('mean_absolute_errors')is not None]
            scores['matched_shape_mae_'+key]=float(np.mean(values))if values else None
        values=[x['morphology_stability']['foreground_dice']for x in good if x.get('morphology_stability',{}).get('foreground_dice')is not None]
        scores['rotation_stability_dice']=float(np.mean(values))if values else None
        patients.append({'patient_id':pid,'frozen_fovs':len(rows),'scored_fovs':len(good),'metrics':scores})
    macro={k:float(np.mean([p['metrics'][k]for p in patients]))if all(p['metrics'][k]is not None for p in patients)else None for k in patients[0]['metrics']}if patients else{}
    return {'patient_rows':patients,'equal_patient_macro':macro,'patient_denominator':len(patients),
        'all_frozen_fovs_scored':all(r['status']=='SCORED'for r in results),'population_inference':False,'confidence_interval':None}


def check():
    selection=verify_selection();intake=read(D/'intake.json')
    if intake['selection_sha256']!=file_hash(D/'selection.json'):raise ValueError('Selection changed after intake')
    for c in intake['cases']:
        for asset in c['assets'].values():
            candidate=P/asset['path'];path=candidate.resolve()
            if not path.is_relative_to(D)or candidate.is_symlink()or file_hash(path)!=asset['sha256']:raise ValueError('Asset changed or escaped')
    return {'status':'READY_FOR_ROOT_QUEUE','patients':selection['patients'],'frozen_fovs':6,
            'dimension_valid_fovs':sum(c['status']=='READY_REFERENCE_CONTENT_UNOPENED'for c in intake['cases']),
            'dimension_valid_patients':len({c['patient_id']for c in intake['cases']if c['status']=='READY_REFERENCE_CONTENT_UNOPENED'}),
            'annotation_values_opened':False,'accuracy_computed':False}


def export_metadata():
    """Audit source header mismatches without annotation or pixel-value reads."""
    check();result=[]
    for c in read(D/'intake.json')['cases']:
        match=re.search(r'_left-(\d+)_top-(\d+)_bottom-(\d+)_right-(\d+)$',c['fov_id'])
        left,top,bottom,right=map(int,match.groups());w,h=c['image_size_xy'];mw,mh=c['mask_size_xy']
        result.append({'fov_id':c['fov_id'],'filename_window':{'left':left,'top':top,'bottom':bottom,'right':right},
            'window_extent':[right-left,bottom-top],'rgb_size':[w,h],'mask_size':[mw,mh],'rgb_minus_mask':[w-mw,h-mh]})
    return {'status':'METADATA_ONLY_NO_ALIGNMENT_REPAIR','cases':result,
        'next_primary_review':'Inspect authors original RGB export and annotation rasterization code for crop-bound inclusivity and scale rounding. Compare all_fov_locations metadata if publicly available, before any image/mask transformation proposal.',
        'source_repository':'https://github.com/PathologyDataScience/NuCLS',
        'unknowns':['Native slide pixel size for filename coordinate window','RGB resampling rounding convention','Mask rasterizer output extent convention'],
        'forbidden_in_current_frozen_run':'No resize, padding, cropping, remapping or case substitution. Any justified correction requires a separately frozen protocol and exact source-image transform.'}


def run():
    from PIL import Image
    check();protocol=read(D/'protocol.json')
    for name,expected in protocol['input_sha256'].items():
        if file_hash(P/name)!=expected:raise ValueError('Frozen experiment input changed')
    OUT.mkdir(parents=True,exist_ok=False);start=time.monotonic();intake=read(D/'intake.json');predictions={};results=[]
    try:
        for case in intake['cases']:
            if case['status']!='READY_REFERENCE_CONTENT_UNOPENED':continue
            rgb=np.asarray(Image.open(P/case['assets']['image']['path']).convert('RGB'));labels=predict_instances(rgb)
            directory=OUT/case['fov_id'];directory.mkdir()
            np.save(directory/'predicted_instances.npy',labels,allow_pickle=False)
            rotated=np.rot90(predict_instances(np.rot90(rgb)),k=-1);a=labels>0;b=rotated>0
            stability={'transform':'90_degree_rotation_then_inverse','foreground_dice':float(2*(a&b).sum()/(a.sum()+b.sum()))if a.sum()+b.sum()else None,
                'count_difference':int(rotated.max())-int(labels.max()),'scope':'Same fixed image-only baseline; geometric equivariance diagnostic, not biological repeatability.'}
            predictions[case['fov_id']]={'path':str((directory/'predicted_instances.npy').relative_to(P)),
                'sha256':file_hash(directory/'predicted_instances.npy'),'stability':stability}
        write(OUT/'prediction_commitment.json',{'at':utc_now(),'protocol_sha256':file_hash(D/'protocol.json'),'references_opened':False,'predictions':predictions})
        for case in intake['cases']:
            row={'patient_id':case['patient_id'],'slide_id':case['slide_id'],'fov_id':case['fov_id'],'annotation_version':case['annotation_version']}
            if case['fov_id']not in predictions:results.append({**row,'status':case['status']});continue
            pred=predictions[case['fov_id']];labels=np.load(P/pred['path'],allow_pickle=False)
            try:
                rows=read_annotations(P/case['assets']['annotations']['path'],labels.shape);mask=np.asarray(Image.open(P/case['assets']['mask']['path']))
                metrics=score_measurements(labels,rows,mask,[.2,.2]);metrics['scope']='One frozen NuCLS FOV within a three-participant grouped evaluation; no clinical or P09 molecular endpoint.'
                results.append({**row,'status':'SCORED','metrics':metrics,'morphology':morphology(labels,rows,mask,[.2,.2]),
                    'morphology_stability':pred['stability'],'annotation_history':case['annotation_history']})
            except ValueError as e:results.append({**row,'status':'INVALID_REFERENCE_NO_REPLACEMENT','reason':str(e)})
        write(OUT/'results.json',{'status':'EVALUATION_COMPLETE','fovs':results,'grouped':aggregate(results),'clinical_validation':False,'P09_genomic_pairing':False,
            'annotation_revision_accuracy':'NOT_ESTABLISHED_NO_DATED_PREDECESSOR','protocol_sha256':file_hash(D/'protocol.json')})
        write(OUT/'receipt.json',{'status':'COMPLETE','wall_seconds':time.monotonic()-start,'maxrss_KiB':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'frozen_fovs':6,'scored_fovs':sum(r['status']=='SCORED'for r in results),'patients':3,'reference_reads_after_prediction_commitment':True,
            'scored_patients':len({r['patient_id']for r in results if r['status']=='SCORED'}),
            'input_sha256':protocol['input_sha256'],'artifact_sha256':{str(x.relative_to(OUT)):file_hash(x)for x in OUT.rglob('*')if x.is_file()},'paid_usd':0,'download_bytes':0})
    except BaseException as e:
        write(OUT/'failure.json',{'status':'FAILED','error':repr(e),'wall_seconds':time.monotonic()-start,'automatic_retry':False});raise


def main():
    ap=argparse.ArgumentParser(description=__doc__);a=ap.add_mutually_exclusive_group(required=True)
    for name in ['freeze','intake','check','export-metadata','run']:a.add_argument('--'+name,action='store_true')
    args=ap.parse_args();resource.setrlimit(resource.RLIMIT_AS,(8*1024**3,8*1024**3))
    def expired(*_):raise TimeoutError('Finite pathology task limit')
    signal.signal(signal.SIGALRM,expired);signal.alarm(270)
    try:
        if args.freeze:freeze()
        elif args.intake:intake()
        elif args.check:print(json.dumps(check()))
        elif args.export_metadata:print(json.dumps(export_metadata(),indent=2))
        else:run()
    finally:signal.alarm(0)


if __name__=='__main__':main()
