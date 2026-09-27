#!/usr/bin/env python3
"""Frozen v13 documentary benchmark; --check never calls a comparator."""
from __future__ import annotations
import os
for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[key] = "1"
import argparse
import json
from pathlib import Path
import resource
import signal
import time
from invariantcell.nih_revision_v13 import (METHODS, file_sha, verify_freeze, validate_corpus,
                                           solve, evaluate_predictions, validate_anchors)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/post_confirmation_v13/nih_revision"


def save(path, obj):
    with path.open("x") as stream:
        json.dump(obj, stream, indent=2, allow_nan=False)
        stream.write("\n")


def main():
    p = argparse.ArgumentParser()
    actions = p.add_mutually_exclusive_group(required=True)
    actions.add_argument("--check", action="store_true")
    actions.add_argument("--run", action="store_true")
    p.add_argument("--output", type=Path)
    a = p.parse_args()
    signal.alarm(180)
    resource.setrlimit(resource.RLIMIT_CPU, (180, 180))
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    freeze = verify_freeze(ROOT, DATA / "freeze.json")
    corpora = [json.loads((DATA / split / "corpus.json").read_text())
               for split in ("development", "validation", "final_internal_test")]
    corpus = {"records": [r for c in corpora for r in c["records"]],
              "queries": [q for c in corpora for q in c["queries"]],
              "anchors": {k: v for c in corpora for k, v in c["anchors"].items()}}
    check = validate_corpus(corpus)
    check["source_anchors_verified"] = validate_anchors(ROOT, corpus)
    if a.check:
        print(json.dumps({"status": "FROZEN_READY", **check, "solver_run": False,
                          "freeze_sha256": file_sha(DATA / "freeze.json")}))
        return
    if a.output is None or not a.output.resolve().is_relative_to(ROOT / "runs/post_confirmation_v13"):
        p.error("--run requires a new output within runs/post_confirmation_v13")
    # A fixed once-only marker is acquired before any comparator or labels.
    marker = DATA / "evaluation_attempt.json"
    a.output.resolve().mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    save(marker, {"output": str(a.output.resolve().relative_to(ROOT)), "freeze_sha256": file_sha(DATA / "freeze.json")})
    try:
        predictions, runtimes = {}, {}
        for method in METHODS:
            begin = time.monotonic()
            predictions[method] = {q["id"]: solve(corpus["records"], q, method) for q in corpus["queries"]}
            runtimes[method] = time.monotonic() - begin
        # Persist predictions before opening evaluator-only gold.
        save(a.output / "predictions.json", predictions)
        gold = {k: v for split in ("development", "validation", "final_internal_test")
                for k, v in json.loads((DATA / "evaluator_only" / (split + "_gold.json")).read_text()).items()}
        metrics = evaluate_predictions(corpus, predictions, gold)
        save(a.output / "results.json", {"status": "COMPLETE", "scope": "RETROSPECTIVE_EDITORIAL_EVIDENCE_REVIEW",
             "freeze_sha256": file_sha(DATA / "freeze.json"), "metrics": metrics, "runtime_seconds": runtimes,
             "predictions_sha256": file_sha(a.output / "predictions.json"),
             "wall_seconds": time.monotonic() - started, "maxrss_KiB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
             "expert_gold": False, "biological_validation": False, "eligibility_established": False,
             "old_benchmarks_rerun": False, "protocol": freeze["protocol"]})
    except BaseException as error:
        save(a.output / "failure.json", {"status": "FAILED_ATTEMPT_CONSUMED", "error": repr(error),
             "wall_seconds": time.monotonic() - started, "maxrss_KiB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss})
        raise
    print(a.output / "results.json")


if __name__ == "__main__":
    main()
