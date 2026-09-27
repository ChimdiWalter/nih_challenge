"""Preregistered NuCLS intake and quantity-specific patient benchmark, v14."""
from __future__ import annotations
import os
for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[key] = '1'
os.environ['CUDA_VISIBLE_DEVICES'] = ''
import argparse, csv, hashlib, json, re, resource, signal, sys, time, urllib.parse, urllib.request
from pathlib import Path
P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P / 'src'))
from invariantcell.io import file_hash, utc_now
ROOT_DATA = P / 'data/pathology/v14'
D = ROOT_DATA
R = P / 'reports/pathology/v14'
LIMIT = 128 * 1024 ** 2
EXCLUDED = ['TCGA-A1-A0SP', 'TCGA-A2-A0CM', 'TCGA-A2-A0D0', 'TCGA-A2-A0D2']
OUT = R / 'run-patient-benchmark-v1'
BASELINE_SHA = '329b9165d044e16778395cd69f6dd209fd9a9e8545b37385a1929d01e1ef55c4'

def read(path):
    return json.loads(Path(path).read_text())

def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        json.dump(value, f, indent=2, allow_nan=False); f.write('\n')

def fetch(url, path, cap=4*1024**2):
    """Public GET, bounded immutable receipts, no retries after failure."""
    path = Path(path); receipt = Path(str(path) + '.download.json')
    if path.exists():
        old = read(receipt)
        if old['url'] != url or old['sha256'] != file_hash(path):
            raise ValueError('Changed cached asset')
        return old
    failure = Path(str(path) + '.failure.json')
    if failure.exists(): raise ValueError('Retained intake failure; no automatic retry')
    used = sum(read(p).get('bytes', 0) for p in ROOT_DATA.rglob('*.download.json'))
    used += sum(read(p).get('bytes', 0) for p in ROOT_DATA.rglob('*.failure.json'))
    if used + cap > LIMIT: raise ValueError('Download reservation exceeds 128 MiB')
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = Path(str(path) + '.partial'); n = 0; start = time.monotonic()
    try:
        request = urllib.request.Request(url, headers={'User-Agent':'InvariantCell-public-intake/14'})
        with urllib.request.urlopen(request, timeout=30) as response, partial.open('xb') as f:
            while True:
                block = response.read(min(65536, cap + 1 - n))
                if not block: break
                n += len(block)
                if n > cap: raise ValueError('File cap exceeded')
                f.write(block)
            final_url = response.url; status = response.status; mime = response.headers.get('Content-Type')
        partial.rename(path)
        result = dict(url=url, final_url=final_url, http_status=status, content_type=mime,
                      bytes=n, sha256=file_hash(path), retrieved_at=utc_now(), wall_seconds=time.monotonic()-start)
        write(receipt, result); return result
    except BaseException as error:
        write(failure, dict(url=url, bytes=n, error=repr(error), automatic_retry=False)); raise

def listing(path):
    from bs4 import BeautifulSoup
    rows = {}
    for row in BeautifulSoup(Path(path).read_text(), 'html.parser').select('tr[data-id]'):
        node = row.select_one('strong')
        if node:
            name = node.get_text(); stem = Path(name).stem
            if stem in rows: raise ValueError('Duplicate metadata identity')
            rows[stem] = {'download_id':row['data-id'], 'filename':name}
    return rows

def embedded_listing(path):
    from bs4 import BeautifulSoup
    rows = {}
    for link in BeautifulSoup(Path(path).read_text(), 'html.parser').find_all('a', href=True):
        name = link.get_text(); match = re.search(r'/file/d/([^/]+)/', link['href'])
        if match and name.startswith('TCGA-'):
            stem = Path(name).stem; patient(stem)
            if stem in rows: raise ValueError('Duplicate metadata identity')
            rows[stem] = {'download_id':match[1], 'filename':name}
    return rows

def patient(stem):
    match = re.fullmatch(r'(TCGA-[A-Z0-9]{2}-[A-Z0-9]{4})-(DX\d+)_id-[a-z0-9]+_left-\d+_top-\d+_bottom-\d+_right-\d+', stem)
    if not match: raise ValueError('Unexpected participant/slide identifier')
    return match[1], match[1] + '-' + match[2]

def metadata():
    folders = {'image':'1IyFVpB87VnX1Xjshyoj9TfpwPgTdorcF', 'annotations':'1ovjc4bLlzNS5y0kOq3eN6tvH0mAywXvG', 'mask':'1ZpmnSL0_PQcuZgzHyNN-CBGLVErN8XZM'}
    for role, fid in folders.items():
        path = D / 'metadata' / (role + '_reverse.html')
        fetch('https://drive.google.com/drive/folders/' + fid + '?sort=13&direction=d', path)
        rows = listing(path)
        print(role, len(rows), sorted({patient(s)[0] for s in rows}))

def candidate_panel(listings, excluded=EXCLUDED, participants=8, per_patient=12):
    common = set.intersection(*(set(x) for x in listings.values())); groups = {}
    for stem in sorted(common):
        pid, slide = patient(stem)
        if pid not in excluded:
            groups.setdefault(pid, []).append({'patient_id':pid, 'slide_id':slide, 'fov_id':stem,
                'assets':{role:table[stem] for role,table in listings.items()}})
    if len(groups) < participants: raise ValueError('Insufficient disjoint participants')
    return {pid:groups[pid][:per_patient] for pid in sorted(groups)[:participants]}

def preregister():
    if (D/'preregistration.json').exists(): raise ValueError('Preregistration exists')
    paths = {r:ROOT_DATA/'metadata'/('embedded_'+r+'.html') for r in ('image','annotations','mask')}
    tables = {r:embedded_listing(path) for r,path in paths.items()}
    geometric_edge = D != ROOT_DATA
    prior = read(ROOT_DATA/'preregistration.json') if geometric_edge else None
    excluded = EXCLUDED + sorted(prior['candidate_panel']) if prior else EXCLUDED
    panel = candidate_panel(tables,excluded=excluded)
    ids = sorted(panel)
    query = {'op':'in','content':{'field':'submitter_id','value':ids}}
    url = 'https://api.gdc.cancer.gov/cases?' + urllib.parse.urlencode({
        'filters':json.dumps(query),'fields':'case_id,submitter_id,project.project_id','size':'20','format':'JSON'})
    fetch(url,D/'metadata/gdc_cases.json',1024**2)
    hits = read(D/'metadata/gdc_cases.json')['data']['hits']
    crosswalk = {x['submitter_id']:x['case_id'] for x in hits}
    if set(crosswalk)!=set(ids) or len(set(crosswalk.values()))!=len(ids): raise ValueError('Unresolved patient identity')
    if any(x['project']['project_id']!='TCGA-BRCA' for x in hits): raise ValueError('Unexpected specimen project')
    slidepath = P/'data/pathology/v13_nucls/metadata/nucls_slide_names.txt'
    slides = slidepath.read_text()
    for cases in panel.values():
        for c in cases:
            if c['slide_id'] not in slides: raise ValueError('Slide missing from author list')
            c['gdc_case_uuid'] = crosswalk[c['patient_id']]
    pins = {str(p.relative_to(P)):file_hash(p) for p in paths.values()}
    sources = [D/'metadata/gdc_cases.json',slidepath,
        P/'data/pathology/v13_nucls/metadata/gdc_barcode.html',
        P/'data/post_confirmation_v10/pathology/nucls_format.txt',
        P/'data/post_confirmation_v10/pathology/nucls_single_rater.txt',
        P/'src/invariantcell/pathology_v10.py', P/'scripts/pathology_v13.py']
    pins.update({str(p.relative_to(P)):file_hash(p) for p in sources})
    if geometric_edge:
        source = P/'data/pathology/v13_nucls_export_review/nucls_model__torchvision_detection_utils__transforms.py'
        pins[str(source.relative_to(P))]=file_hash(source)
        pins[str((ROOT_DATA/'preregistration.json').relative_to(P))]=file_hash(ROOT_DATA/'preregistration.json')
        pins[str((ROOT_DATA/'selection.json').relative_to(P))]=file_hash(ROOT_DATA/'selection.json')
    snapshot=D/'preregistered_intake_source.txt'
    with snapshot.open('x') as f:f.write(Path(__file__).read_text())
    pins[str(snapshot.relative_to(P))]=file_hash(snapshot)
    write(D/'preregistration.json',{
        'schema':'pathology-v14-preregistration-1','at':utc_now(),'status':'FROZEN_BEFORE_NEW_IMAGE_OR_REFERENCE_INTAKE',
        'candidate_panel':panel,'candidate_participants':8,'candidate_fov_cap_per_patient':12,'target_participants':5,
        'excluded_participants':excluded,'selection_order':'Lexicographic participant, then lexicographic FOV ID.',
        'eligibility_rule':{'rgb_mask_native_size_identical':True,'rgb_mask_mode':'RGB','minimum_dimension':32,
            'maximum_dimension':2048,'allow_geometric_outer_edge':geometric_edge,
            'coordinates':'Every box minimum inside image; maximum and polygon vertices may equal width/height geometric outer edge; existing frozen parser/rasterizer clips only to native pixel lattice, no source edits or resizing.' if geometric_edge else 'Every box and vertex strictly within pixel-index bounds; no clamping, resizing or adaptation.',
            'positive_reference':'At least one non-unlabeled polyline with nonzero bounding extent.',
            'headers_and_annotation_type_bounds_only':True},
        'stopping_rule':'First eligible FOV per participant, first five qualifying participants among frozen eight; at most first twelve FOVs per participant. Log every attempted candidate and any failure. No case changes after selection commit or any scoring. If fewer than five qualify, stop as insufficient intake, do not expand.',
        'reference_exposure':'Eligibility opens CSV type/classification/coordinates and image/mask headers. No image/mask pixel arrays or reference metric calculation during intake. No predictions or scores.',
        'prior_metadata_learning':'Strict-screen panel preserved; metadata-only discovery showed geometric endpoints equal width/height, allowed by author helper and existing v10 parser. This new panel excludes all eight strict-screen candidate patients, not just downloaded cases.' if geometric_edge else None,
        'pixel_size_um':[.2,.2],'grouping':'Public TCGA participant barcode and GDC case UUID; diagnostic breast specimen slide (DX), no clinical or molecular join.',
        'baseline':'Unchanged hematoxylin_otsu_components_v1; no fitted model or selection using model scores.',
        'quantity_contract':{'count_localization':'All labeled rectangles and polylines, centroid inside annotated region; localization box IoU >=0.5.',
            'pixel_area':'Only polyline-supported positive truth; rectangle/unlabeled regions excluded. Area is restricted eligible-area, never total nuclear area.',
            'morphology':'Conditional errors only for matched fully valid non-border polylines; report eligible count, otherwise undefined.'},
        'claims':'New small patient-disjoint measurement benchmark relative to old four participants; selection-conditioned, nonrepresentative, no clinical/diagnostic claim. Source corrected references independent of these predictions, not independent manual retracing: NuCLS used reviewed algorithmic suggestions.',
        'rights':{'data_license':'CC0-1.0','source':'https://sites.google.com/view/nucls/single-rater','institutional_or_prize_rights_established':False},
        'history':'Corrected single-rater content version recorded by hash; annotation/specimen/public-availability timestamps unknown; no dated annotation revision replay.',
        'input_sha256':pins,'outputs_computed':False})
    print(json.dumps({'preregistration':str((D/'preregistration.json').relative_to(P)),
                      'sha256':file_hash(D/'preregistration.json'),'patients':ids,'candidate_fovs':sum(map(len,panel.values()))}))

def inspect_geometry(image_path, mask_path, annotations_path, allow_geometric_outer_edge=False):
    """Metadata eligibility only; never produces a prediction or an accuracy metric."""
    from PIL import Image
    with Image.open(image_path) as im: size=im.size; mode=im.mode
    with Image.open(mask_path) as im: mask_size=im.size; mask_mode=im.mode
    result={'image_size_xy':list(size),'mask_size_xy':list(mask_size),'image_mode':mode,'mask_mode':mask_mode}
    reasons=[]
    if size!=mask_size or mode!='RGB' or mask_mode!='RGB': reasons.append('NATIVE_DIMENSION_OR_MODE_MISMATCH')
    if min(size)<32 or max(size)>2048: reasons.append('DIMENSION_OUTSIDE_CAP')
    width,height=size; types={'rectangle':0,'polyline':0}; positive_polylines=0
    try:
        with Path(annotations_path).open(newline='') as f:
            rows=list(csv.DictReader(f))
        if not rows: reasons.append('EMPTY_REFERENCE')
        for row in rows:
            kind=row['type']
            if kind not in types: reasons.append('UNSUPPORTED_GEOMETRY'); continue
            types[kind]+=1
            x0,y0,x1,y1=(int(row[k]) for k in ('xmin','ymin','xmax','ymax'))
            xs=[int(v) for v in row['coords_x'].split(',')];ys=[int(v) for v in row['coords_y'].split(',')]
            xmax=width if allow_geometric_outer_edge else width-1
            ymax=height if allow_geometric_outer_edge else height-1
            if not(0<=x0<width and x0<=x1<=xmax and 0<=y0<height and y0<=y1<=ymax): reasons.append('BOX_OUTSIDE_COORDINATE_BOUNDS')
            if len(xs)!=len(ys) or len(xs)<3: reasons.append('INVALID_VERTEX_ARRAY')
            if any(not(0<=x<=xmax and 0<=y<=ymax) for x,y in zip(xs,ys)): reasons.append('VERTEX_OUTSIDE_COORDINATE_BOUNDS')
            if kind=='polyline' and row['raw_classification']!='unlabeled' and x1>x0 and y1>y0: positive_polylines+=1
    except (KeyError,ValueError,UnicodeError) as error:
        reasons.append('MALFORMED_REFERENCE:'+type(error).__name__)
    if positive_polylines<1: reasons.append('NO_POSITIVE_POLYLINE_REFERENCE')
    return {**result,'annotation_types':types,'positive_labeled_polylines':positive_polylines,
            'status':'ELIGIBLE' if not reasons else 'INELIGIBLE','reasons':sorted(set(reasons))}

def verify_pins(pins):
    for name,expected in pins.items():
        candidate=P/name; path=candidate.resolve()
        if candidate.is_symlink() or not path.is_relative_to(P) or file_hash(path)!=expected:
            raise ValueError('Changed or escaped pinned input: '+name)

def intake():
    prereg=read(D/'preregistration.json');verify_pins(prereg['input_sha256'])
    if (D/'selection.json').exists(): raise ValueError('Selection already committed; no intake rerun')
    chosen=[];attempts=[];start=time.monotonic()
    for pid,cases in prereg['candidate_panel'].items():
        for case in cases:
            attempt_path=D/'intake_attempts'/(case['fov_id']+'.json')
            if attempt_path.exists():
                attempt=read(attempt_path)
                verify_pins({a['path']:a['sha256'] for a in attempt.get('assets',{}).values()})
            else:
                assets={}
                try:
                    for role,asset in case['assets'].items():
                        path=D/'assets'/case['fov_id']/(role+('.csv' if role=='annotations' else '.png'))
                        receipt=fetch('https://drive.google.com/uc?export=download&id='+asset['download_id'],path)
                        assets[role]={'path':str(path.relative_to(P)),**receipt}
                    inspection=inspect_geometry(*(P/assets[r]['path'] for r in ('image','mask','annotations')),
                        allow_geometric_outer_edge=prereg['eligibility_rule'].get('allow_geometric_outer_edge',False))
                    attempt={**case,'assets':assets,**inspection,'at':utc_now(),'preregistration_sha256':file_hash(D/'preregistration.json')}
                except Exception as error:
                    attempt={**case,'assets':assets,'status':'INTAKE_FAILED','error':repr(error),'at':utc_now(),
                        'preregistration_sha256':file_hash(D/'preregistration.json')}
                write(attempt_path,attempt)
            attempts.append(attempt)
            print(json.dumps({'participant':pid,'fov':case['fov_id'],'status':attempt['status'],'reasons':attempt.get('reasons',[])}),flush=True)
            if attempt['status']=='ELIGIBLE':chosen.append(attempt);break
        if len(chosen)==prereg['target_participants']:break
    pins={str(p.relative_to(P)):file_hash(p) for p in D.rglob('*') if p.is_file()}
    write(D/'selection.json',{'status':'FROZEN_READY' if len(chosen)==5 else 'INSUFFICIENT_QUALIFIED_PARTICIPANTS',
        'at':utc_now(),'preregistration_sha256':file_hash(D/'preregistration.json'),'selected':chosen,
        'attempted_fovs':len(attempts),'attempted_patients':sorted({x['patient_id']for x in attempts}),
        'attempt_results':[{'fov_id':x['fov_id'],'patient_id':x['patient_id'],'status':x['status'],'reasons':x.get('reasons',[])}for x in attempts],
        'selected_patients':len(chosen),'no_predictions_or_scores':True,'input_sha256':pins,
        'intake_wall_seconds':time.monotonic()-start,'bytes_downloaded':sum(read(p)['bytes']for p in D.rglob('*.download.json')),
        'selection_scope':'Header/type/bounds-qualified subpopulation; full screened denominator retained separately from five-patient measurement denominator.'})
    print(json.dumps({'selection_status':read(D/'selection.json')['status'],'selected':len(chosen),'attempted':len(attempts)}))

def moments(binary, scale=(.2,.2)):
    import numpy as np
    y,x=np.where(binary);n=len(x)
    if n<2:return None
    a=np.column_stack([x*scale[0],y*scale[1]]);a-=a.mean(axis=0)
    v=np.maximum(np.linalg.eigvalsh(a.T@a/n),0)
    return {'area_um2':float(n*np.prod(scale)),'major_axis_um':float(4*np.sqrt(v[1])),
        'minor_axis_um':float(4*np.sqrt(v[0])), 'eccentricity':float(np.sqrt(1-v[0]/v[1]))if v[1]>0 else 0.}

def score_quantities(labels, rows, mask, scale=(.2,.2)):
    """Separate eligible domains; boxes can never supply positive pixel truth."""
    import numpy as np
    from PIL import Image, ImageDraw
    from invariantcell.pathology_v10 import references, instance_boxes, match_objects
    if labels.ndim!=2 or mask.shape!=(*labels.shape,3) or not np.issubdtype(labels.dtype,np.integer) or (labels<0).any():
        raise ValueError('Invalid measurement arrays')
    truth,valid,roi,objects=references(rows,mask)
    unknown=Image.new('1',(labels.shape[1],labels.shape[0]));drawer=ImageDraw.Draw(unknown)
    for row in rows:
        if row['classification']=='unlabeled':drawer.rectangle(row['box'],fill=1)
    object_roi=roi & (mask[:,:,0]!=99) & ~np.asarray(unknown,dtype=bool)
    boxes,ids=instance_boxes(labels,object_roi)
    ref=[r for r in objects if object_roi[(r['box'][1]+r['box'][3])//2,(r['box'][0]+r['box'][2])//2]]
    matching=match_objects(boxes,[r['box']for r in ref],.5)
    count={'reference':len(ref),'predicted':len(boxes),'signed_error':len(boxes)-len(ref),
        'absolute_error':abs(len(boxes)-len(ref)), 'relative_absolute_error':abs(len(boxes)-len(ref))/len(ref)if ref else None,
        'scope':'Labeled objects with centroid in annotated region excluding unknown reference regions; not total tissue cell count.'}
    pred=(labels>0)&valid;tp=int((pred&truth).sum());fp=int((pred&~truth).sum());fn=int((~pred&truth).sum())
    positive=int(truth.sum());pred_area=int(pred.sum());supported=positive>0
    pixel={'status':'ELIGIBLE' if supported else 'UNDEFINED_NO_POSITIVE_POLYLINE_PIXELS','reference_positive_pixels':positive,
        'eligible_pixels':int(valid.sum()),'excluded_rectangle_or_unknown_pixels':int((roi&~valid).sum()),
        'tp':tp,'fp':fp,'fn':fn,'dice':2*tp/(2*tp+fp+fn)if supported else None,
        'iou':tp/(tp+fp+fn)if supported else None,
        'scope':'Polyline positive pixels and remaining annotated background; rectangle and unknown regions excluded. Background completeness is a dataset assumption.'}
    area={'status':pixel['status'],'reference_um2':positive*float(np.prod(scale))if supported else None,
        'predicted_um2':pred_area*float(np.prod(scale))if supported else None,
        'absolute_error_um2':abs(pred_area-positive)*float(np.prod(scale))if supported else None,
        'scope':'Eligible polyline/background domain only; not total nuclear area or box area.'}
    shapes=[];excluded=0;centroids=[]
    for match in matching['matches']:
        row=ref[match['reference_index']];box=boxes[match['prediction_index']]
        cx=(box[0]+box[2]-row['box'][0]-row['box'][2])/2*scale[0]
        cy=(box[1]+box[3]-row['box'][1]-row['box'][3])/2*scale[1]
        centroids.append(float(np.hypot(cx,cy)))
        if row['type']!='polyline':excluded+=1;continue
        canvas=Image.new('1',(labels.shape[1],labels.shape[0]));ImageDraw.Draw(canvas).polygon(row['vertices'],fill=1)
        binary=np.asarray(canvas,dtype=bool);inferred=labels==ids[match['prediction_index']]
        def border(a):return bool(a[0].any()or a[-1].any()or a[:,0].any()or a[:,-1].any())
        if (binary&~valid).any()or(inferred&~valid).any()or border(binary)or border(inferred):excluded+=1;continue
        a=moments(inferred,scale);b=moments(binary,scale)
        if a is None or b is None:excluded+=1;continue
        shapes.append({'source_row':row['source_row'],'predicted':a,'reference':b,'absolute_errors':{k:abs(a[k]-b[k])for k in a}})
    morphology={'status':'ELIGIBLE'if shapes else 'UNDEFINED_NO_FULL_VALID_MATCHED_POLYLINES',
        'eligible_pairs':len(shapes),'excluded_matches':excluded,'pairs':shapes,
        'mean_absolute_errors':{k:float(np.mean([r['absolute_errors'][k]for r in shapes]))for k in shapes[0]['absolute_errors']}if shapes else None,
        'scope':'Matched complete non-border polyline objects only; conditional shape accuracy, not all-object morphology.'}
    return {'count':count,'localization':{**matching,'box_iou_threshold':.5,
                'matched_box_center_distance_mean_um':float(np.mean(centroids))if centroids else None},
        'pixel':pixel,'area':area,'morphology':morphology,
        'reference_types':{'polyline':sum(r['type']=='polyline'for r in ref),'rectangle':sum(r['type']=='rectangle'for r in ref)},
        'physical_scale_um':list(scale)}

def patient_summary(cases, patients):
    import numpy as np
    accessors={'count_absolute_error':lambda m:m['count']['absolute_error'],
        'localization_f1':lambda m:m['localization']['f1'],'pixel_dice':lambda m:m['pixel']['dice'],
        'eligible_area_absolute_error_um2':lambda m:m['area']['absolute_error_um2']}
    for key in ('area_um2','major_axis_um','minor_axis_um','eccentricity'):
        accessors['matched_shape_mae_'+key]=lambda m,k=key:(m['morphology']['mean_absolute_errors']or{}).get(k)
    groups=[]
    for pid in patients:
        rows=[x for x in cases if x['patient_id']==pid];good=[x for x in rows if x['status']=='SCORED'];values={}
        for name,getter in accessors.items():
            items=[getter(row['measurements'])for row in good]
            values[name]=float(np.mean(items))if items and all(x is not None for x in items)and len(good)==len(rows) else None
        groups.append({'patient_id':pid,'selected_fovs':len(rows),'scored_fovs':len(good),'metrics':values})
    summary={}
    for name in accessors:
        available=[r['metrics'][name]for r in groups if r['metrics'][name]is not None]
        summary[name]={'equal_patient_macro':float(np.mean(available))if len(available)==len(patients)else None,
            'eligible_patients':len(available),'fixed_patient_denominator':len(patients),
            'available_patient_mean_descriptive':float(np.mean(available))if available else None}
    return {'patients':groups,'metrics':summary,'confidence_intervals':None,'population_inference':False,
        'note':'Five independent participant identifiers, one selected FOV each; not representative sampling. Quantity eligibility denominators differ and missing participants are never silently removed.'}

def selection_check():
    prereg=read(D/'preregistration.json');selection=read(D/'selection.json')
    if selection['preregistration_sha256']!=file_hash(D/'preregistration.json'):raise ValueError('Preregistration changed')
    verify_pins(prereg['input_sha256']);verify_pins(selection['input_sha256'])
    if file_hash(P/'src/invariantcell/pathology_v10.py')!=BASELINE_SHA:raise ValueError('Frozen baseline changed')
    if selection['status']!='FROZEN_READY' or selection['no_predictions_or_scores']is not True:raise ValueError('Intake not ready')
    cases=selection['selected'];patients=[x['patient_id']for x in cases]
    if len(cases)!=5 or len(set(patients))!=5 or set(patients)&set(prereg['excluded_participants']):raise ValueError('Not five disjoint new participants')
    # Reconstruct the predeclared first-eligible stopping rule from immutable intake records.
    chosen=[];attempts=[]
    for pid,candidates in prereg['candidate_panel'].items():
        for candidate in candidates:
            attempt=read(D/'intake_attempts'/(candidate['fov_id']+'.json'));attempts.append(attempt)
            if attempt['preregistration_sha256']!=file_hash(D/'preregistration.json'):raise ValueError('Unbound intake attempt')
            if attempt['status']=='ELIGIBLE':chosen.append(attempt);break
        if len(chosen)==5:break
    if chosen!=cases or len(attempts)!=selection['attempted_fovs']:raise ValueError('Selection differs from frozen screening order')
    return selection

def finalize():
    selection=selection_check();prereg=read(D/'preregistration.json')
    pins={**prereg['input_sha256'],**selection['input_sha256']}
    for path in [D/'selection.json',D/'preregistration.json',Path(__file__),P/'tests/test_pathology_v14.py']:
        pins[str(path.relative_to(P))]=file_hash(path)
    from invariantcell.pathology_v10 import RECIPE
    write(D/'protocol.json',{'schema':'pathology-v14-measurement-1','at':utc_now(),'status':'FROZEN_BEFORE_PREDICTIONS',
        'selection_sha256':file_hash(D/'selection.json'),'input_sha256':pins,'recipe':RECIPE,
        'patients':[c['patient_id']for c in selection['selected']],'fov_ids':[c['fov_id']for c in selection['selected']],
        'pixel_size_um':[.2,.2],'output':str(OUT.relative_to(P)),
        'quantity_contract':{'count':'Labeled rectangles and polylines; centroid in FOV excluding unlabeled/unknown regions.',
            'localization':'Maximum-cardinality box IoU matching at fixed 0.5, then IoU tie preference; center-distance diagnostic conditional on match.',
            'pixel':'Rasterized labeled polylines only; rectangles and unknown regions excluded. Positive-reference absence makes Dice/IoU undefined, never a positive segmentation validation.',
            'area':'Absolute inferred vs polygon foreground area on same eligible domain at 0.04 um^2/pixel; not total area.',
            'morphology':'Matched full valid non-border polygon objects: area, second-moment major/minor axes and eccentricity MAE; no matches means undefined.'},
        'aggregation':'Equal patient weighting, fixed five-patient denominator; report strict macro only when all five eligible, plus explicitly partial descriptive means and denominators.',
        'prediction_freeze':'Commit all five image-only predictions before mask pixel access or reference scoring. CSV metadata was already exposed for eligibility and is disclosed.',
        'no_training_or_tuning':True,'no_case_replacement':True,'no_source_mutation_or_resizing':True,
        'native_coordinate_convention':prereg['eligibility_rule']['coordinates'],
        'scope':'Independent archived corrected references, assisted by author algorithms; retrospective source-version measurement, not blinded clinical validation.'})
    pins[str((D/'protocol.json').relative_to(P))]=file_hash(D/'protocol.json')
    task={'name':'pathology-v14-five-new-patient-measurement','boundary':'post_confirmation_v11','purpose':'pathology',
        'script':'scripts/pathology_v14.py','args':['--panel','geometric-edge','--run']if D!=ROOT_DATA else ['--run'],'input_sha256':pins,'outputs':[str(OUT.relative_to(P))],
        'completion_test':'receipt.status COMPLETE, results.status EVALUATION_COMPLETE, scored_fovs=5, selected_patients=5; five-prediction commitment precedes scoring; all screening failures preserved; each quantity reports five-patient denominator and eligible counts; no clinical/model-gain claim.',
        'resources':{'cpu_threads':1,'ram_gib':2,'wall_seconds':300,'workspace_growth_bytes':32*1024**2,'download_bytes':0,'gpu_hours':0,'paid_usd':0},'status':'READY'}
    write(R/'task.json',task)
    print(json.dumps(check()))

def check():
    selection=selection_check();protocol=read(D/'protocol.json');verify_pins(protocol['input_sha256'])
    if protocol['selection_sha256']!=file_hash(D/'selection.json'):raise ValueError('Selection binding changed')
    if protocol['output']!=str(OUT.relative_to(P)):raise ValueError('Output changed')
    if OUT.exists():raise ValueError('Output already exists; no automatic rerun')
    return {'status':'READY_FOR_ROOT_SERIAL_QUEUE','selected_patients':5,'selected_fovs':5,
        'screened_fovs':selection['attempted_fovs'],'protocol_sha256':file_hash(D/'protocol.json'),
        'prediction_or_accuracy_computed':False,'network_required':False}

def run():
    import numpy as np
    from PIL import Image
    from invariantcell.pathology_v10 import predict_instances,read_annotations
    check();selection=read(D/'selection.json');protocol=read(D/'protocol.json')
    start=time.monotonic();OUT.mkdir(parents=True,exist_ok=False);predictions={};results=[]
    try:
        for case in selection['selected']:
            with Image.open(P/case['assets']['image']['path'])as im:rgb=np.asarray(im)
            labels=predict_instances(rgb);path=OUT/(case['patient_id']+'-instances.npy')
            with path.open('xb')as f:np.save(f,labels,allow_pickle=False)
            predictions[case['fov_id']]={'path':str(path.relative_to(P)),'sha256':file_hash(path)}
        write(OUT/'prediction_commitment.json',{'at':utc_now(),'protocol_sha256':file_hash(D/'protocol.json'),
            'predictions':predictions,'mask_pixel_arrays_opened':False,'reference_scores_computed':False,
            'prior_exposure':'CSV type/classification/geometry and RGB/mask headers used only for predeclared intake eligibility.'})
        for case in selection['selected']:
            row={k:case[k]for k in ('patient_id','slide_id','fov_id','gdc_case_uuid')}
            row['annotation_sha256']=case['assets']['annotations']['sha256']
            try:
                prediction=predictions[case['fov_id']]
                if file_hash(P/prediction['path'])!=prediction['sha256']:raise ValueError('Prediction commitment changed')
                labels=np.load(P/prediction['path'],allow_pickle=False)
                rows=read_annotations(P/case['assets']['annotations']['path'],labels.shape)
                with Image.open(P/case['assets']['mask']['path'])as im:mask=np.asarray(im)
                measurements=score_quantities(labels,rows,mask)
                results.append({**row,'status':'SCORED','measurements':measurements})
            except Exception as error:results.append({**row,'status':'FAILED_NO_REPLACEMENT','error':repr(error)})
        complete=all(x['status']=='SCORED'for x in results)and len(results)==5
        write(OUT/'results.json',{'status':'EVALUATION_COMPLETE'if complete else 'INCOMPLETE_WITH_RETAINED_FAILURES',
            'fovs':results,'patient_grouped':patient_summary(results,protocol['patients']),
            'screening':{k:selection[k]for k in ('attempted_fovs','attempted_patients','attempt_results')},
            'protocol_sha256':file_hash(D/'protocol.json'),'clinical_validation':False,'fresh_confirmation':False,
            'model_improvement_claim':False,'dated_annotation_revision_accuracy':'NOT_ESTABLISHED'})
        write(OUT/'receipt.json',{'status':'COMPLETE'if complete else 'FAILED','scored_fovs':sum(x['status']=='SCORED'for x in results),
            'selected_patients':5,'wall_seconds':time.monotonic()-start,'maxrss_KiB':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'protocol_sha256':file_hash(D/'protocol.json'),'input_sha256':protocol['input_sha256'],
            'artifact_sha256':{str(p.relative_to(OUT)):file_hash(p)for p in OUT.rglob('*')if p.is_file()},
            'download_bytes':0,'gpu_hours':0,'paid_usd':0,'automatic_retry':False})
        if not complete:raise RuntimeError('Preserved measurement failure; no retry')
    except BaseException as error:
        write(OUT/'failure.json',{'status':'FAILED','error':repr(error),'wall_seconds':time.monotonic()-start,
            'maxrss_KiB':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'automatic_retry':False});raise

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); group=parser.add_mutually_exclusive_group(required=True)
    parser.add_argument('--panel',choices=['strict','geometric-edge'],default='strict')
    group.add_argument('--metadata', action='store_true');group.add_argument('--preregister',action='store_true');group.add_argument('--intake',action='store_true')
    for name in ('finalize','check','run'):group.add_argument('--'+name,action='store_true')
    args = parser.parse_args()
    if args.panel=='geometric-edge':
        D=ROOT_DATA/'geometric_edge_panel';OUT=R/'run-geometric-edge-panel-v1'
    resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
    def timeout(*unused):raise TimeoutError('Finite v14 pathology limit')
    signal.signal(signal.SIGALRM,timeout);signal.alarm(900 if args.intake else 270)
    if args.metadata: metadata()
    elif args.preregister:preregister()
    elif args.intake:intake()
    elif args.finalize:finalize()
    elif args.check:print(json.dumps(check()))
    elif args.run:run()
