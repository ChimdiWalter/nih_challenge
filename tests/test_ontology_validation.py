"""Optional real-parser/SHACL/OWL-RL integration tests on synthetic RDF only."""
import unittest
from unittest.mock import patch

from invariantcell.ontology_mapping import parse_obo, parse_gaf, map_annotations, export_ntriples
from invariantcell.ontology_validation import (
    dependency_status, parse_ntriples, validate_shapes, check_owlrl, validate_ntriples,
)


def fixture_text():
    obo = """format-version: 1.2
[Term]
id: GO:0000001
name: synthetic child
namespace: molecular_function
is_a: GO:0000002
[Term]
id: GO:0000002
name: synthetic parent
namespace: molecular_function
"""
    rows = ["\t".join(["UniProtKB", "SYNTHETIC", "SYN", relation, "GO:0000001", "GO_REF:synthetic", "IDA", "", "F", "", "",
                        "protein", "taxon:9606", "20200101", "UniProt", "", ""])
            for relation in ("enables", "NOT|enables")]
    gaf = "!gaf-version: 2.2\n!generated-by: fixture\n!date-generated: 2020-01-02\n" + "\n".join(rows)
    return export_ntriples(map_annotations(parse_gaf(gaf, "synthetic", synthetic=True),
                                           parse_obo(obo, "synthetic-go", synthetic=True)))


@unittest.skipUnless(dependency_status()["available"], "optional project-local ontology packages not on PYTHONPATH")
class OntologyValidationTests(unittest.TestCase):
    def setUp(self):
        from rdflib import Namespace
        from rdflib.namespace import RDF, OWL, XSD, RDFS
        self.RDF, self.OWL, self.XSD, self.RDFS = RDF, OWL, XSD, RDFS
        self.B = Namespace("https://w3id.org/biolink/vocab/")
        self.IC = Namespace("urn:invariantcell:ontology-mapping:")
        self.EX = Namespace("urn:synthetic:")
        self.graph = parse_ntriples(fixture_text())

    def test_independent_rdf_parse_and_serialization_round_trip(self):
        from rdflib.compare import isomorphic
        self.assertGreater(len(self.graph), 20)
        self.assertTrue(isomorphic(self.graph, parse_ntriples(self.graph.serialize(format="nt"))))
        report = validate_ntriples(fixture_text())
        self.assertTrue(report["rdf_parse"]["valid"])
        self.assertTrue(report["shacl"]["conforms"])
        self.assertEqual(report["owlrl"]["status"], "NO_CONTRADICTION_DETECTED")

    def test_parse_failure_does_not_report_shape_or_logic_success(self):
        report = validate_ntriples("this is not RDF")
        self.assertFalse(report["rdf_parse"]["valid"])
        self.assertEqual(report["shacl"]["status"], "NOT_RUN_PARSE_FAILED")
        self.assertEqual(report["owlrl"]["status"], "NOT_RUN_PARSE_FAILED")

    def test_shape_failure_is_separate_from_logical_contradiction(self):
        subject = next(self.graph.subjects(self.RDF.type, self.B.Association))
        self.graph.remove((subject, self.B.negated, None))
        shape = validate_shapes(self.graph)
        logical, _ = check_owlrl(self.graph)
        self.assertFalse(shape["conforms"])
        self.assertTrue(any(v["path"] == str(self.B.negated) for v in shape["violations"]))
        self.assertEqual(logical["status"], "NO_CONTRADICTION_DETECTED")

    def test_shacl_conformance_does_not_imply_owlrl_consistency(self):
        self.graph.add((self.EX.A, self.OWL.disjointWith, self.EX.B))
        self.graph.add((self.EX.x, self.RDF.type, self.EX.A))
        self.graph.add((self.EX.x, self.RDF.type, self.EX.B))
        self.assertTrue(validate_shapes(self.graph)["conforms"])
        logical, _ = check_owlrl(self.graph)
        self.assertEqual(logical["status"], "CONTRADICTION_DETECTED")
        self.assertTrue(any("Disjoint classes" in error for error in logical["rule_errors"]))
        self.assertEqual(logical["full_owl_consistency"], "NOT_EVALUATED")

    def test_actual_owlrl_entailment_can_reveal_disjoint_type_conflict(self):
        self.graph.add((self.EX.Child, self.RDFS.subClassOf, self.EX.Parent))
        self.graph.add((self.EX.Parent, self.OWL.disjointWith, self.EX.Other))
        self.graph.add((self.EX.x, self.RDF.type, self.EX.Child))
        self.graph.add((self.EX.x, self.RDF.type, self.EX.Other))
        logical, expanded = check_owlrl(self.graph)
        self.assertIn((self.EX.x, self.RDF.type, self.EX.Parent), expanded)
        self.assertNotIn((self.EX.x, self.RDF.type, self.EX.Parent), self.graph)
        self.assertEqual(logical["status"], "CONTRADICTION_DETECTED")

    def test_not_reification_never_asserts_positive_or_negative_bare_edges(self):
        from rdflib import Literal
        before = set(self.graph)
        _, expanded = check_owlrl(self.graph)
        negative = list(self.graph.subjects(self.B.negated, Literal(True)))
        self.assertEqual(len(negative), 1)
        association = negative[0]
        subject = self.graph.value(association, self.RDF.subject)
        predicate = self.graph.value(association, self.RDF.predicate)
        obj = self.graph.value(association, self.RDF.object)
        self.assertNotIn((subject, predicate, obj), expanded)
        self.assertFalse(list(expanded.subjects(self.RDF.type, self.OWL.NegativePropertyAssertion)))
        validate_shapes(self.graph)
        self.assertEqual(before, set(self.graph))

    def test_not_qualifier_mismatch_fails_real_shacl(self):
        from rdflib import Literal
        negative = next(self.graph.subjects(self.B.negated, Literal(True)))
        self.graph.remove((negative, self.IC.gaf_qualifier, Literal("NOT", datatype=self.XSD.string)))
        self.assertFalse(validate_shapes(self.graph)["conforms"])

    def test_missing_association_type_still_targeted_and_rejected(self):
        association = next(self.graph.subjects(self.RDF.type, self.B.Association))
        self.graph.remove((association, self.RDF.type, self.B.Association))
        report = validate_shapes(self.graph)
        self.assertEqual(report["association_targets"], 2)
        self.assertFalse(report["conforms"])

    def test_empty_graph_does_not_present_vacuous_shapes_as_covered(self):
        from rdflib import Graph
        report = validate_shapes(Graph())
        self.assertEqual(report["status"], "NO_ASSOCIATION_TARGETS")
        self.assertEqual(report["association_targets"], 0)

    def test_imports_are_not_loaded_and_no_network_needed(self):
        from rdflib import URIRef
        self.graph.add((self.EX.ontology, self.OWL.imports, URIRef("https://invalid.example/do-not-fetch")))
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            logical, _ = check_owlrl(self.graph)
            self.assertTrue(validate_shapes(self.graph)["conforms"])
        self.assertEqual(logical["imports_not_loaded"], ["https://invalid.example/do-not-fetch"])

    def test_resource_limits_are_failures_not_fake_success(self):
        with self.assertRaisesRegex(ValueError, "byte budget"):
            parse_ntriples(fixture_text(), max_bytes=1)
        with self.assertRaisesRegex(ValueError, "triple budget"):
            parse_ntriples(fixture_text(), max_triples=1)
        report, _ = check_owlrl(self.graph, max_triples=1)
        self.assertEqual(report["status"], "INCOMPLETE_BUDGET")
        self.assertFalse(report["completed"])
        self.assertIsNone(report["contradictions_detected"])
        report, _ = check_owlrl(self.graph, max_cycles=1)
        self.assertEqual(report["status"], "INCOMPLETE_BUDGET")


if __name__ == "__main__":
    unittest.main()
