"""Replay, provenance, and time-axis tests on explicitly synthetic records."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch

from invariantcell.core import BiologicalTransition, DependencyRef, canonical_hash
from invariantcell.temporal import (EvidenceRecord, Snapshot, TemporalQuery, evaluate, issue_certificate,
                                    transition_to_evidence, snapshot_from_transitions, TemporalOutcome)
from invariantcell.replay import verify_certificate

PAST = "2020-01-01T00:00:00Z"
CUTOFF = "2021-01-01T00:00:00Z"
FUTURE = "2022-01-01T00:00:00Z"


def evidence(evidence_id="e1", **kwargs):
    defaults = dict(subject="target", predicate="affects", value="pathway", context="context",
                    polarity="supports", public_available_at=PAST, ingested_at=PAST,
                    source_sha256="a" * 64, synthetic=True, validity_kind="interval")
    defaults.update(kwargs)
    return EvidenceRecord(evidence_id=evidence_id, **defaults)


def query(**kwargs):
    defaults = dict(subject="target", predicate="affects", context="context",
                    evidence_cutoff=CUTOFF, ingestion_cutoff=CUTOFF, valid_at=CUTOFF)
    defaults.update(kwargs)
    return TemporalQuery(**defaults)


def rehash(certificate):
    certificate["snapshot_sha256"] = canonical_hash(certificate["snapshot"])
    certificate["dependencies_sha256"] = canonical_hash(certificate["snapshot"]["dependencies"])
    certificate["query_sha256"] = canonical_hash(certificate["query"])
    certificate["certificate_sha256"] = canonical_hash({k: v for k, v in certificate.items() if k != "certificate_sha256"})


class TemporalTests(unittest.TestCase):
    def test_public_availability_and_ingestion_are_independent_axes(self):
        snapshot = Snapshot("synthetic", (evidence(ingested_at=FUTURE),))
        self.assertFalse(evaluate(snapshot, query()).answers)
        self.assertTrue(evaluate(snapshot, query(ingestion_cutoff=FUTURE)).answers)
        snapshot = Snapshot("synthetic", (evidence(public_available_at=FUTURE, ingested_at=PAST),))
        self.assertFalse(evaluate(snapshot, query(ingestion_cutoff=FUTURE)).answers)

    def test_valid_interval_is_half_open_and_separate_from_event_time(self):
        snapshot = Snapshot("synthetic", (evidence(valid_from=PAST, valid_to=CUTOFF, event_time=None),))
        self.assertFalse(evaluate(snapshot, query()).answers)
        self.assertTrue(evaluate(snapshot, query(valid_at=PAST)).answers)
        self.assertFalse(evaluate(snapshot, query(valid_at=PAST, event_cutoff=CUTOFF)).answers)
        self.assertIsNone(snapshot.evidence[0].event_time)

    def test_future_unknown_and_unresolved_dependencies_fail_closed(self):
        for available in (FUTURE, None):
            dependency = DependencyRef("mapping", "b" * 64, available, "mapping")
            snapshot = Snapshot("synthetic", (evidence(dependencies=("mapping",)),), (dependency,))
            result = evaluate(snapshot, query())
            self.assertFalse(result.answers)
            self.assertIn("dependency_after_cutoff_or_unknown", result.excluded[0][1])
        with self.assertRaisesRegex(ValueError, "unresolved"):
            Snapshot("synthetic", (evidence(dependencies=("missing",)),))

    def test_model_cutoff_is_global_and_in_certificate(self):
        model = DependencyRef("model", "c" * 64, FUTURE, "model")
        snapshot = Snapshot("synthetic", (evidence(),), (model,))
        result = evaluate(snapshot, query())
        self.assertFalse(result.answers)
        self.assertEqual(result.model_cutoff_violations, ("model",))
        self.assertEqual(result.outcome, TemporalOutcome.REJECTED_DEPENDENCY)
        verified = verify_certificate(issue_certificate(snapshot, query()), snapshot,
                                      canonical_hash(snapshot.to_dict()), query())
        self.assertFalse(verified.valid)
        self.assertIn("model_dependencies_violate_cutoff", verified.errors)

    def test_conflicts_remain_visible_and_context_does_not_bleed(self):
        snapshot = Snapshot("synthetic", (evidence("supports"), evidence("refutes", polarity="refutes"),
                                           evidence("other", context="other", value="different")))
        result = evaluate(snapshot, query())
        self.assertEqual(len(result.answers), 1)
        self.assertEqual(result.answers[0].status, "conflicted")
        self.assertEqual(result.outcome, TemporalOutcome.CONFLICT)
        self.assertEqual(result.answers[0].support_ids, ("supports",))
        self.assertEqual(result.answers[0].refute_ids, ("refutes",))

    def test_prediction_is_not_silently_promoted_to_observation(self):
        snapshot = Snapshot("synthetic", (evidence(state_kind="predicted"),))
        self.assertFalse(evaluate(snapshot, query()).answers)
        self.assertTrue(evaluate(snapshot, query(allow_predictions=True)).answers)

    def test_replay_is_independent_of_producer_execution(self):
        snapshot = Snapshot("synthetic", (evidence(),))
        intended = query()
        certificate = issue_certificate(snapshot, intended)
        with patch("invariantcell.temporal.evaluate", side_effect=AssertionError("producer must not run")):
            result = verify_certificate(certificate, snapshot, canonical_hash(snapshot.to_dict()), intended)
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(snapshot, Snapshot.from_dict(snapshot.to_dict()))

    def test_tampering_query_answer_source_time_and_model_is_rejected_even_if_rehashed(self):
        model = DependencyRef("model", "c" * 64, PAST, "model")
        snapshot = Snapshot("synthetic", (evidence(),), (model,))
        certificate = issue_certificate(snapshot, query())
        root = canonical_hash(snapshot.to_dict())
        mutations = [
            lambda c: c["query"].update(context="different"),
            lambda c: c["result"]["answers"][0].update(value="invented"),
            lambda c: c["snapshot"]["evidence"][0].update(source_sha256="d" * 64),
            lambda c: c["snapshot"]["evidence"][0].update(public_available_at=FUTURE),
            lambda c: c["snapshot"]["dependencies"][0].update(sha256="e" * 64),
        ]
        for mutation in mutations:
            changed = deepcopy(certificate)
            mutation(changed)
            rehash(changed)
            self.assertFalse(verify_certificate(changed, snapshot, root, query()).valid)

    def test_self_consistent_forgery_cannot_replace_external_trust_root(self):
        trusted = Snapshot("synthetic", (evidence(),))
        forged = Snapshot("synthetic", (evidence(value="invented"),))
        certificate = issue_certificate(forged, query())
        self.assertFalse(verify_certificate(certificate, trusted, canonical_hash(trusted.to_dict()), query()).valid)
        self.assertFalse(verify_certificate(certificate, forged, canonical_hash(trusted.to_dict()), query()).valid)

    def test_missing_external_query_anchor_is_not_an_optional_check(self):
        snapshot = Snapshot("synthetic", (evidence(),))
        with self.assertRaises(TypeError):
            verify_certificate(issue_certificate(snapshot, query()), snapshot, canonical_hash(snapshot.to_dict()))

    def test_unknown_public_or_ingestion_times_exclude_evidence(self):
        for field in ("public_available_at", "ingested_at"):
            snapshot = Snapshot("synthetic", (evidence(**{field: None}),))
            self.assertFalse(evaluate(snapshot, query()).answers)


    def test_unknown_validity_cannot_answer_valid_at_or_be_inferred_from_other_dates(self):
        item = evidence(validity_kind="unknown", event_time=PAST)
        snapshot = Snapshot("synthetic", (item,))
        result = evaluate(snapshot, query())
        self.assertFalse(result.answers)
        self.assertIn("unknown_validity", result.excluded[0][1])
        self.assertEqual(result.outcome, TemporalOutcome.ABSTAIN_UNSUPPORTED)
        self.assertTrue(verify_certificate(issue_certificate(snapshot, query()), snapshot,
                                           canonical_hash(snapshot.to_dict()), query()).valid)
        with self.assertRaisesRegex(ValueError, "explicit interval"):
            evidence(validity_kind="unknown", valid_from=PAST)

    def test_open_validity_bounds_require_explicit_assertion(self):
        item = EvidenceRecord("default", "target", "affects", "pathway", "context", "supports",
                              PAST, PAST, "a" * 64, synthetic=True)
        self.assertEqual(item.validity_kind, "unknown")
        self.assertFalse(evaluate(Snapshot("synthetic", (item,)), query()).answers)
        for bounds in ({}, {"valid_from": PAST}, {"valid_to": FUTURE}):
            declared = replace(item, validity_kind="interval", **bounds)
            self.assertTrue(evaluate(Snapshot("synthetic", (declared,)), query()).answers)

    def test_transition_bridge_retains_prediction_identity_hash_and_time_axes(self):
        dependency = DependencyRef("training", "b" * 64, PAST)
        transition = BiologicalTransition("prediction", "context", "target", ("gene",), ((7,),),
                                          state_kind="predicted", synthetic=True, dependencies=(dependency,))
        snapshot = snapshot_from_transitions(
            (transition,), "synthetic", generated_at=PAST, public_available_at=CUTOFF,
            ingested_at=FUTURE, validity_kind="interval", valid_from=PAST, valid_to=FUTURE)
        item = snapshot.evidence[0]
        self.assertEqual(item.source_sha256, canonical_hash(transition.to_dict()))
        self.assertEqual(item.transition_record_id, transition.record_id)
        self.assertEqual(item.perturbation, transition.perturbation)
        self.assertEqual((item.context, item.subject, item.predicate, item.value),
                         ("context", "target", "has_count_record", "prediction"))
        self.assertEqual(item.state_kind, "predicted")
        self.assertTrue(item.synthetic)
        self.assertIsNone(item.event_time)
        self.assertEqual(item.generated_at, PAST)
        self.assertEqual(item.public_available_at, CUTOFF)
        self.assertEqual(item.ingested_at, FUTURE)
        self.assertIn(dependency, snapshot.dependencies)
        intended = query(predicate="has_count_record", ingestion_cutoff=FUTURE, allow_predictions=True)
        self.assertTrue(evaluate(snapshot, intended).answers)
        self.assertFalse(evaluate(snapshot, replace(intended, allow_predictions=False)).answers)
        self.assertTrue(verify_certificate(issue_certificate(snapshot, intended), snapshot,
                                           canonical_hash(snapshot.to_dict()), intended).valid)

    def test_transition_bridge_does_not_fill_missing_times_or_dependency_availability(self):
        dependency = DependencyRef("training", "b" * 64, None)
        transition = BiologicalTransition("prediction", "context", "target", ("gene",), ((7,),),
                                          state_kind="predicted", synthetic=True, dependencies=(dependency,))
        item = transition_to_evidence(transition, generated_at=PAST)
        self.assertIsNone(item.public_available_at)
        self.assertIsNone(item.ingested_at)
        self.assertIsNone(item.event_time)
        self.assertIsNone(item.valid_from)
        self.assertEqual(item.validity_kind, "unknown")
        snapshot = snapshot_from_transitions((transition,), "synthetic", generated_at=PAST,
                                             public_available_at=PAST, ingested_at=PAST,
                                             validity_kind="interval")
        self.assertIn(dependency, snapshot.dependencies)
        result = evaluate(snapshot, query(predicate="has_count_record", allow_predictions=True))
        self.assertFalse(result.answers)
        self.assertIn("dependency_after_cutoff_or_unknown", result.excluded[0][1])

    def test_transition_bridge_retains_observed_control_and_rejects_impossible_prediction_dates(self):
        transition = BiologicalTransition("control", "context", None, ("gene",), ((7,),),
                                          synthetic=True, event_time=PAST, evidence_available_at=CUTOFF,
                                          ingested_at=FUTURE)
        item = transition_to_evidence(transition)
        self.assertEqual(item.subject, "control")
        self.assertIsNone(item.perturbation)
        self.assertEqual(item.state_kind, "observed")
        self.assertEqual(item.event_time, PAST)
        self.assertEqual(item.public_available_at, CUTOFF)
        self.assertEqual(item.ingested_at, FUTURE)
        prediction = replace(transition, record_id="prediction", perturbation="target", state_kind="predicted")
        with self.assertRaisesRegex(ValueError, "precede"):
            transition_to_evidence(prediction, generated_at=FUTURE, public_available_at=PAST)

    def test_transition_snapshot_rejects_inconsistent_dependency_identity(self):
        first = BiologicalTransition("first", "context", "target", ("gene",), ((7,),), synthetic=True,
                                      dependencies=(DependencyRef("source", "a" * 64, PAST),))
        second = replace(first, record_id="second", dependencies=(DependencyRef("source", "b" * 64, PAST),))
        with self.assertRaisesRegex(ValueError, "conflicting"):
            snapshot_from_transitions((first, second), "synthetic")


    def test_certificate_does_not_call_prediction_only_answer_observed_support(self):
        intended = query(allow_predictions=True)
        snapshot = Snapshot("synthetic", (evidence(state_kind="predicted"),))
        certificate = issue_certificate(snapshot, intended)
        self.assertEqual(certificate["result"]["outcome"], "prediction_with_valid_provenance")
        self.assertEqual(certificate["result"]["answers"][0]["status"], "prediction_with_valid_provenance")
        self.assertEqual(certificate["result"]["answers"][0]["support_state_kinds"], ["predicted"])
        root = canonical_hash(snapshot.to_dict())
        self.assertTrue(verify_certificate(certificate, snapshot, root, intended).valid)
        forged = deepcopy(certificate)
        forged["result"]["outcome"] = "observed_support"
        forged["result"]["answers"][0]["status"] = "observed_support"
        rehash(forged)
        self.assertFalse(verify_certificate(forged, snapshot, root, intended).valid)
        observed = Snapshot("synthetic", (evidence(state_kind="observed"),))
        self.assertEqual(evaluate(observed, intended).outcome, TemporalOutcome.OBSERVED_SUPPORT)


if __name__ == "__main__":
    unittest.main()
