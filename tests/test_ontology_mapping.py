"""Synthetic contract fixtures; these are not GO biological accuracy evidence."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
import unittest
from unittest.mock import patch

from invariantcell.core import canonical_hash
from invariantcell.logic import verify
from invariantcell.ontology_mapping import (
    BIOLINK_SCHEMA_SHA256, check_ontology_logic, check_ontology_shapes, export_ntriples,
    map_annotations, mapping_contract, ontology_logic_inputs, parse_gaf, parse_obo, to_temporal_snapshot,
)
from invariantcell.replay import verify_certificate
from invariantcell.temporal import TemporalQuery, evaluate, issue_certificate

PAST = "2019-01-01T00:00:00Z"
CUTOFF = "2021-01-01T00:00:00Z"
FUTURE = "2022-01-01T00:00:00Z"
OBO = """format-version: 1.2
data-version: synthetic-go-2019

[Term]
id: GO:0000001
name: synthetic activity one
namespace: molecular_function
alt_id: GO:0000091
is_a: GO:0000002 ! synthetic parent

[Term]
id: GO:0000002
name: synthetic activity two
namespace: molecular_function
is_a: GO:0000003

[Term]
id: GO:0000003
name: synthetic activity root
namespace: molecular_function

[Term]
id: GO:0000004
name: synthetic process
namespace: biological_process

[Term]
id: GO:0000005
name: synthetic component
namespace: cellular_component

[Term]
id: GO:0000006
name: obsolete synthetic activity
namespace: molecular_function
is_obsolete: true
replaced_by: GO:0000001
consider: GO:0000002
"""


def row(**changes):
    columns = ["UniProtKB", "SYNTHETIC1", "FIXTURE", "enables", "GO:0000001", "GO_REF:synthetic", "IDA", "",
               "F", "Synthetic fixture protein", "", "protein", "taxon:9606", "20180102", "UniProt", "", ""]
    names = {"qualifier": 3, "go_id": 4, "evidence": 6, "with_from": 7, "aspect": 8, "kind": 11,
             "taxa": 12, "date": 13, "extension": 15, "form": 16}
    for key, value in changes.items():
        columns[names[key]] = value
    return "\t".join(columns)


def gaf_text(*rows, version="2.2"):
    return (f"!gaf-version: {version}\n!generated-by: synthetic-fixture\n!date-generated: 2018-01-03\n"
            + "\n".join(rows) + "\n")


def make_export(*rows, public=PAST, ingested=PAST, ontology_available=PAST):
    return map_annotations(parse_gaf(gaf_text(*(rows or (row(),))), "gaf-fixture", public_available_at=public,
                                     ingested_at=ingested, synthetic=True),
                           parse_obo(OBO, "go-fixture", public_available_at=ontology_available,
                                     ingested_at=PAST, synthetic=True))


def bridge(export, *, mapping_available=PAST, intervals=True, predicted=False):
    return to_temporal_snapshot(export, "synthetic-go", context="fixture-context",
                                mapping_available_at=mapping_available, mapping_ingested_at=PAST,
                                validity_by_id={a.id: (PAST, None) for a in export.associations} if intervals else None,
                                state_kind_by_id={a.id: "predicted" if predicted else "observed" for a in export.associations})


def query(snapshot, **changes):
    item = snapshot.evidence[0]
    return TemporalQuery(**{**dict(subject=item.subject, predicate=item.predicate, context=item.context,
                                  evidence_cutoff=CUTOFF, ingestion_cutoff=CUTOFF, valid_at=CUTOFF), **changes})


class OntologyMappingTests(unittest.TestCase):
    def test_gaf_lossless_columns_qualifiers_forms_and_independent_date(self):
        raw = row(qualifier="NOT|contributes_to", evidence="ISS", with_from="UniProtKB:X,UniProtKB:Y|UniProtKB:Z",
                  extension="part_of(CL:0000001),occurs_in(UBERON:0000001)|part_of(CL:0000002)",
                  form="UniProtKB:SYNTHETIC1-2", date="20160229")
        document = parse_gaf(gaf_text(raw), "fixture", synthetic=True)
        item = document.annotations[0]
        self.assertEqual("\t".join(item.columns), raw)
        self.assertEqual(item.qualifiers, ("NOT", "contributes_to"))
        self.assertTrue(item.negated)
        self.assertEqual(item.subject, "UniProtKB:SYNTHETIC1-2")
        self.assertEqual(item.annotation_date, "2016-02-29")
        self.assertIsNone(document.source.public_available_at)
        self.assertIsNone(document.source.ingested_at)

    def test_structural_gaf_failures_are_explicit(self):
        malformed = [gaf_text(row(date="20190229")), gaf_text(row(date="2019-01-01")),
                     gaf_text(row()).replace("\tGO:0000001\t", "\tGO:1\t"),
                     gaf_text(row(qualifier="NOT")), gaf_text(row(qualifier="enables|NOT")),
                     gaf_text(row(qualifier="NOT|NOT|enables")), gaf_text(row(qualifier="enables|part_of")),
                     gaf_text(row(taxa="NCBITaxon:9606")), gaf_text(row(form="not a CURIE")),
                     gaf_text(row() + "\textra"), row() + "\n", gaf_text(row(), version="1.0"),
                     gaf_text(row()).replace("!generated-by: synthetic-fixture\n", "")]
        for text in malformed:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_gaf(text, "bad")

    def test_legacy_unspecified_relation_is_preserved_not_invented(self):
        gaf = parse_gaf(gaf_text(row(qualifier="NOT"), version="2.1"), "legacy")
        mapped = map_annotations(gaf, parse_obo(OBO, "go")).associations[0]
        self.assertTrue(mapped.negated)
        self.assertIsNone(mapped.predicate)
        self.assertIn("unmapped_relation", mapped.issues)

    def test_exact_biolink_mapping_does_not_type_protein_as_gene(self):
        mapped = make_export(row(qualifier="involved_in", go_id="GO:0000004", aspect="P")).associations[0]
        self.assertEqual(mapped.subject_category, "biolink:Protein")
        self.assertEqual(mapped.predicate, "biolink:actively_involved_in")
        self.assertEqual(mapped.object_category, "biolink:BiologicalProcess")
        self.assertEqual(BIOLINK_SCHEMA_SHA256, "37146d876aee83c1be58baf7da5a5bc97d6f3a5f5d5017484cf3b6cba0ece305")
        self.assertFalse(mapped.issues)

    def test_obsolete_replacements_aliases_and_namespace_survive_export(self):
        export = make_export(row(go_id="GO:0000091"), row(go_id="GO:0000006"))
        alias, obsolete = export.associations
        self.assertEqual(alias.object, "GO:0000001")
        self.assertEqual(alias.annotation.columns[4], "GO:0000091")
        self.assertEqual(obsolete.object, "GO:0000006")
        self.assertIn("obsolete_go_term", obsolete.issues)
        term = export.ontology.index()["GO:0000006"]
        self.assertEqual(term.replaced_by, ("GO:0000001",))
        self.assertEqual(term.consider, ("GO:0000002",))
        self.assertEqual(json.loads(json.dumps(export.to_dict()))["ontology"]["terms"][-1]["namespace"],
                         "molecular_function")
        mapped = bridge(export)
        self.assertEqual(len(mapped.snapshot.evidence), 1)
        self.assertEqual(mapped.excluded[0][0], obsolete.id)

    def test_ambiguous_aliases_invalid_booleans_and_obsolete_axioms_rejected(self):
        invalid = [OBO.replace("id: GO:0000002\n", "id: GO:0000091\n", 1),
                   OBO.replace("is_obsolete: true", "is_obsolete: yes"),
                   OBO.replace("is_obsolete: true", "is_obsolete: false"),
                   OBO.replace("is_obsolete: true", "is_obsolete: true\nis_a: GO:0000001"),
                   OBO.replace("is_a: GO:0000003", 'is_a: GO:0000003 {source="X"}')]
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_obo(text, "bad")

    def test_unknown_axioms_and_imports_are_retained_but_not_reasoned(self):
        raw = OBO.replace("data-version: synthetic-go-2019", "data-version: synthetic-go-2019\nimport: https://invalid.example/import")
        raw = raw.replace("alt_id: GO:0000091", 'alt_id: GO:0000091\ndef: "literal ! retained" []\nrelationship: part_of GO:0000003')
        raw += "\n[Typedef]\nid: synthetic_relation\nname: retained relation\nis_transitive: true\n"
        ontology = parse_obo(raw, "fixture")
        self.assertIn(("def", '"literal ! retained" []'), ontology.terms[0].raw_tags)
        report = check_ontology_logic(ontology)
        self.assertEqual(report["uninterpreted_axiom_tags"], ["relationship"])
        self.assertEqual(report["imports_not_loaded"], ["https://invalid.example/import"])
        self.assertEqual(report["unmodeled_stanza_count"], 1)
        self.assertEqual(report["owl_consistency"], "NOT_EVALUATED")

    def test_shape_failures_do_not_become_logical_successes(self):
        for raw in (OBO.replace("is_a: GO:0000003", "is_a: GO:9999999"),
                    OBO.replace("is_a: GO:0000003", "is_a: GO:0000004")):
            ontology = parse_obo(raw, "fixture")
            self.assertFalse(check_ontology_shapes(ontology)["valid"])
            with self.assertRaisesRegex(ValueError, "shape"):
                check_ontology_logic(ontology)

    def test_horn_entailment_replay_and_tamper_detection(self):
        ontology = parse_obo(OBO, "fixture")
        profile, facts = ontology_logic_inputs(ontology)
        certificate = check_ontology_logic(ontology)["certificate"]
        self.assertIn(["GO:0000001", "rdfs:subClassOf", "GO:0000003"], certificate["facts"])
        self.assertNotIn(["GO:0000003", "rdfs:subClassOf", "GO:0000001"], certificate["facts"])
        with patch("invariantcell.logic.reason", side_effect=AssertionError("no producer replay")):
            self.assertTrue(verify(certificate, profile, facts)["valid"])
        altered = deepcopy(certificate)
        altered["facts"].append(["GO:0000004", "rdfs:subClassOf", "GO:0000001"])
        with self.assertRaises(ValueError):
            verify(altered, profile, facts)

    def test_cycle_is_go_dag_violation_not_claimed_owl_inconsistency(self):
        raw = OBO.replace("name: synthetic activity root", "is_a: GO:0000001\nname: synthetic activity root")
        ontology = parse_obo(raw, "cycle")
        self.assertTrue(check_ontology_shapes(ontology)["valid"])
        report = check_ontology_logic(ontology)
        self.assertFalse(report["go_dag_valid"])
        self.assertEqual(report["owl_consistency"], "NOT_EVALUATED")
        with self.assertRaisesRegex(ValueError, "budget"):
            check_ontology_logic(ontology, max_facts=1)

    def test_unknown_subject_relation_extensions_and_mismatches_fail_closed(self):
        export = make_export(row(qualifier="colocalizes_with"), row(kind="ncRNA"), row(extension="part_of(CL:0000001)"),
                             row(go_id="GO:9999999"), row(aspect="C"), row(go_id="GO:0000004"))
        self.assertEqual(len(export.associations), 6)
        self.assertTrue(all(a.issues for a in export.associations))
        mapped = bridge(export)
        self.assertEqual(len(mapped.excluded), 6)
        self.assertFalse(mapped.snapshot.evidence)

    def test_temporal_state_needs_explicit_assertion_not_guessed_from_code(self):
        export = make_export(row(evidence="IEA"))
        mapped = to_temporal_snapshot(export, "unknown-state", context="fixture")
        self.assertFalse(mapped.snapshot.evidence)
        self.assertEqual(mapped.excluded[0][1], ("evidence_state_not_declared",))
        snapshot = bridge(export, predicted=True).snapshot
        self.assertEqual(snapshot.evidence[0].state_kind, "predicted")
        self.assertFalse(evaluate(snapshot, query(snapshot)).answers)
        self.assertTrue(evaluate(snapshot, query(snapshot, allow_predictions=True)).answers)

    def test_annotation_date_never_fills_availability_event_or_validity(self):
        export = make_export(public=None, ingested=None)
        snapshot = bridge(export, intervals=False).snapshot
        item = snapshot.evidence[0]
        self.assertIsNone(item.public_available_at)
        self.assertIsNone(item.ingested_at)
        self.assertIsNone(item.event_time)
        self.assertIsNone(item.valid_from)
        self.assertEqual(item.validity_kind, "unknown")
        self.assertFalse(evaluate(snapshot, query(snapshot)).answers)
        known = bridge(make_export(), intervals=False).snapshot
        self.assertIn("unknown_validity", evaluate(known, query(known)).excluded[0][1])

    def test_future_or_unknown_source_ontology_mapping_and_ingestion_excluded(self):
        snapshots = [bridge(make_export(public=FUTURE)).snapshot, bridge(make_export(ingested=FUTURE)).snapshot,
                     bridge(make_export(ontology_available=FUTURE)).snapshot,
                     bridge(make_export(ontology_available=None)).snapshot,
                     bridge(make_export(), mapping_available=None).snapshot,
                     bridge(make_export(), mapping_available=FUTURE).snapshot]
        for snapshot in snapshots:
            with self.subTest(snapshot=snapshot):
                self.assertFalse(evaluate(snapshot, query(snapshot)).answers)
        snapshot = snapshots[1]
        self.assertTrue(evaluate(snapshot, query(snapshot, ingestion_cutoff=FUTURE)).answers)

    def test_not_conflict_context_separation_and_temporal_replay(self):
        export = make_export(row(), row(qualifier="NOT|enables"), row(qualifier="NOT|enables", taxa="taxon:10090"))
        snapshot = bridge(export).snapshot
        intended = query(snapshot, context=next(e.context for e in snapshot.evidence
                                                if e.evidence_id == export.associations[0].id))
        result = evaluate(snapshot, intended)
        self.assertEqual(result.answers[0].status, "conflicted")
        self.assertEqual(len(result.answers[0].support_ids), 1)
        self.assertEqual(len(result.answers[0].refute_ids), 1)
        certificate = issue_certificate(snapshot, intended)
        self.assertTrue(verify_certificate(certificate, snapshot, canonical_hash(snapshot.to_dict()), intended).valid)
        changed = deepcopy(certificate)
        changed["snapshot"]["evidence"][0]["source_sha256"] = "f" * 64
        self.assertFalse(verify_certificate(changed, snapshot, canonical_hash(snapshot.to_dict()), intended).valid)

    def test_late_or_unknown_dependency_ingestion_prevents_system_history_leak(self):
        export = make_export()
        for ingested in (FUTURE, None):
            changed = replace(export, ontology=replace(export.ontology,
                              source=replace(export.ontology.source, ingested_at=ingested)))
            snapshot = bridge(changed).snapshot
            self.assertEqual(snapshot.evidence[0].ingested_at, ingested)
            self.assertFalse(evaluate(snapshot, query(snapshot)).answers)
        metadata = {a.id: "observed" for a in export.associations}
        unknown = to_temporal_snapshot(export, "unknown-mapping-ingestion", context="fixture",
                                       mapping_available_at=PAST, state_kind_by_id=metadata,
                                       validity_by_id={a.id: (PAST, None) for a in export.associations}).snapshot
        self.assertIsNone(unknown.evidence[0].ingested_at)
        self.assertFalse(evaluate(unknown, query(unknown)).answers)

    def test_source_hash_binds_raw_evidence_and_metadata_changes(self):
        first, second = make_export(row()), make_export(row(date="20180103"))
        self.assertNotEqual(first.gaf.source.sha256, second.gaf.source.sha256)
        self.assertNotEqual(first.associations[0].id, second.associations[0].id)
        snapshot = bridge(first).snapshot
        self.assertEqual(snapshot.evidence[0].source_sha256, canonical_hash(asdict(first.associations[0])))
        mapping = next(d for d in snapshot.dependencies if d.kind == "mapping")
        self.assertEqual(mapping.sha256, canonical_hash(mapping_contract()))

    def test_rdf_uses_typed_reification_and_never_bare_negated_edges(self):
        export = make_export(row(qualifier="NOT|enables", go_id="GO:0000091"))
        text = export_ntriples(export)
        self.assertEqual(text, export_ntriples(export))
        self.assertIn('"true"^^<http://www.w3.org/2001/XMLSchema#boolean>', text)
        self.assertIn('"2018-01-02"^^<http://www.w3.org/2001/XMLSchema#date>', text)
        self.assertIn('<http://www.w3.org/1999/02/22-rdf-syntax-ns#predicate> <https://w3id.org/biolink/vocab/enables>', text)
        self.assertIn('"GO:0000091"', text)
        self.assertNotIn('<urn:invariantcell:curie:UniProtKB%3ASYNTHETIC1> <https://w3id.org/biolink/vocab/enables>', text)
        self.assertNotIn("valid_from", text)

    def test_naive_times_and_unknown_metadata_identity_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            parse_gaf(gaf_text(row()), "fixture", public_available_at="2020-01-01")
        with self.assertRaisesRegex(ValueError, "unknown annotation"):
            to_temporal_snapshot(make_export(), "fixture", context="x", validity_by_id={"unknown": (None, None)})


if __name__ == "__main__":
    unittest.main()
