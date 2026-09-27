"""Synthetic engineering only: no source final labels are loaded here."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from invariantcell.nih_revision_v13 import solve, METHODS, validate_corpus, verify_freeze, file_sha


class RevisionChecks(unittest.TestCase):
    def setUp(self):
        self.records = []
        for name, sign, start, end in (("old", "refutes", "2020-01-01T00:00:00Z", "2020-02-01T00:00:00Z"),
                                      ("new", "supports", "2020-02-01T00:00:00Z", "2021-01-01T00:00:00Z")):
            self.records.append(dict(evidence_id=name, subject="RID1", predicate="editorially_accepted_revision",
                 value="RID1", context="editorial_workflow", polarity=sign,
                 public_available_at="2020-12-01T00:00:00Z", ingested_at="2020-12-01T00:00:00Z",
                 source_sha256="a" * 64, valid_from=start, valid_to=end, event_time=start,
                 validity_kind="interval"))
        self.q = dict(id="synthetic", split="development", query=dict(subject="RID1",
             predicate="editorially_accepted_revision", context="editorial_workflow",
             evidence_cutoff="2020-12-01T00:00:00Z", ingestion_cutoff="2020-12-01T00:00:00Z",
             valid_at="2020-02-01T00:00:00Z"))

    def test_static_conflates_real_signs_rules_resolve_boundary(self):
        self.assertEqual(solve(self.records, self.q, "static_kg")["outcome"], "CONFLICT")
        for m in METHODS[2:]:
            p = solve(self.records, self.q, m)
            self.assertEqual(p["outcome"], "ACCEPTED")
            self.assertEqual(p["used_ids"], ["new"])
        self.assertTrue(solve(self.records, self.q, METHODS[3])["certificate_replay_valid"])

    def test_first_publication_not_inferred_from_event_date(self):
        self.q["query"]["evidence_cutoff"] = "2020-03-01T00:00:00Z"
        for m in METHODS[1:]:
            self.assertEqual(solve(self.records, self.q, m)["outcome"], "ABSTAIN")

    def test_unknown_event_does_not_pass_cutoff(self):
        self.records[1]["event_time"] = None
        self.q["query"]["event_cutoff"] = "2020-04-01T00:00:00Z"
        for m in METHODS[2:]:
            self.assertEqual(solve(self.records, self.q, m)["outcome"], "ABSTAIN")

    def test_gold_is_not_a_solver_input(self):
        original = solve(self.records, self.q, METHODS[3])
        self.q["gold"] = {"outcome": "fabricated"}
        self.assertEqual(original, solve(self.records, self.q, METHODS[3]))

    def test_substantive_replacement_value_requires_acceptance_time(self):
        r = copy.deepcopy(self.records[1])
        r.update(predicate="accepted_replacement_value", value='[677]')
        self.q["query"]["predicate"] = "accepted_replacement_value"
        self.q["query"]["valid_at"] = "2020-01-20T00:00:00Z"
        self.assertEqual(solve([r], self.q, "static_kg")["outcome"], "VALUE:[677]")
        for m in METHODS[2:]:
            self.assertEqual(solve([r], self.q, m)["outcome"], "ABSTAIN")
            self.q["query"]["valid_at"] = "2020-02-01T00:00:00Z"
            self.assertEqual(solve([r], self.q, m)["outcome"], "VALUE:[677]")
            self.q["query"]["valid_at"] = "2020-01-20T00:00:00Z"

    def test_invalid_interval_and_predictions_rejected(self):
        c = dict(records=copy.deepcopy(self.records), queries=[self.q])
        c["records"][0]["valid_to"] = c["records"][0]["valid_from"]
        with self.assertRaises(ValueError): validate_corpus(c)
        c["records"] = copy.deepcopy(self.records)
        c["records"][0]["state_kind"] = "predicted"
        with self.assertRaises(ValueError): validate_corpus(c)

    def test_freeze_tamper_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / "data").write_text("ok")
            freeze = dict(files={"data": file_sha(root / "data")}, empirical_outputs_examined=False,
                          eligibility_established=False)
            (root / "freeze").write_text(json.dumps(freeze))
            verify_freeze(root, root / "freeze")
            (root / "data").write_text("changed")
            with self.assertRaises(ValueError): verify_freeze(root, root / "freeze")


if __name__ == "__main__": unittest.main()
