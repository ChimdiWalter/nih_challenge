#!/usr/bin/env python3
"""Finite post-confirmation real-image intake (--check) or root-queued scoring."""
from __future__ import annotations
import os
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[key]='1'
os.environ['CUDA_VISIBLE_DEVICES']=''
import argparse,json,resource,signal,time
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'data/post_confirmation_v10/pathology/package.json'

def main():
    p=argparse.ArgumentParser();a=p.add_mutually_exclusive_group(required=True);a.add_argument('--check',action='store_true');a.add_argument('--run',action='store_true')
    p.add_argument('--package',type=Path,default=DEFAULT);p.add_argument('--output',type=Path);args=p.parse_args()
    resource.setrlimit(resource.RLIMIT_CPU,(180,180));signal.alarm(180)
    from invariantcell.pathology_v10 import inspect_package,load_package,sha,predict_instances,read_annotations,score_measurements
    check=inspect_package(args.package)
    if args.check:print(json.dumps(check,indent=2));return
    if args.output is None:p.error('--run requires unique --output under runs/post_confirmation_v10')
    output=args.output.resolve()
    if not output.is_relative_to(ROOT/'runs/post_confirmation_v10'):raise ValueError('output outside post-confirmation boundary')
    output.mkdir(parents=True,exist_ok=False);start=time.monotonic()
    def save(name,obj):
        with (output/name).open('x') as f:json.dump(obj,f,indent=2,allow_nan=False);f.write('\n')
    try:
        import numpy as np
        from PIL import Image
        package,paths=load_package(args.package)
        rgb=np.asarray(Image.open(paths['image']).convert('RGB'))
        labels=predict_instances(rgb) # Does not receive any reference or scale data.
        with (output/'predicted_instances.npy').open('xb') as f:np.save(f,labels,allow_pickle=False)
        save('prediction_receipt.json',{'prediction_sha256':sha(output/'predicted_instances.npy'),'package_sha256':sha(args.package),
             'reference_used_by_predictor':False,'created_at':datetime.now(timezone.utc).isoformat()})
        mask=np.asarray(Image.open(paths['mask']));rows=read_annotations(paths['annotations'],labels.shape)
        metrics=score_measurements(labels,rows,mask,package['pixel_size_um'])
        save('results.json',{'status':'REAL_MEASUREMENT_BASELINE_EVALUATED','check':check,'metrics':metrics,
             'package_sha256':sha(args.package),'prediction_sha256':sha(output/'predicted_instances.npy'),
             'wall_seconds':time.monotonic()-start,'maxrss_KiB':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
             'resource_eligibility':'USER_ATTESTED_NONFEDERAL_DOCUMENTATION_AND_DEPENDENCY_REVIEW_PENDING',
             'clinical_validation':False,'P09_genomic_pairing':False,'prize_eligibility_established':False})
        print(output/'results.json')
    except Exception as exc:
        save('failure.json',{'error':repr(exc),'wall_seconds':time.monotonic()-start,'maxrss_KiB':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss});raise
if __name__=='__main__':main()
