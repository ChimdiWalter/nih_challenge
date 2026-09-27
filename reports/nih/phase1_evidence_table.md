# Phase 1 evidence table — v14

| Evidence | Actual result / status | Immutable source | Permitted interpretation |
|---|---|---|---|
| CIViC final138 | Static24/138; timestamp42/138; rules/verifier138/138 | `runs/post_confirmation_v13/nih-civic-revisions-v1/results.json` | Temporal correctness on fixed documentary queries; no verifier accuracy gain |
| CIViC complete238 | Rules/verifier238/238, including119 correct abstentions | Same source/code/gold freeze `5e6a37349b40b8fae2d4fc4cf8a3dfa70fddcb672c6598edceb81a865a323fe5` | Correlated queries within32 revisions, not238 independent clinical cases |
| Runtime tradeoff | Rules1.029s; verifier5.521s; total9.906s,71460KiB peakRSS | Same actual receipt | Inspectability/replay has a measured runtime cost |
| Final certificate audit | 138/138 saved proofs valid; 138/138 provenance complete; 552/552 specified attacks rejected; 138/138 repeated replays agree | `runs/post_confirmation_v14/nih-certificate-audit-v1/results.json` | Posthoc audit of consumed predictions; no new answers or accuracy trial |
| Audit typed abstentions | 69/69 correct; 69 with typed reasons | Same audit receipt | Unknown first-public availability retained as an abstention reason |
| Audit resource cost | 10.736s; 113592KiB peak RSS | Same audit receipt | Already charged by the root worker; not a repeated experiment |
| Audit engineering | Seven tests pass,0skips,0.069s | `tests/test_nih_certificate_v14.py` | Synthetic contradiction/source-time/tamper checks only |
| ClinVar source family | Seven exact versions; two wrong-version HTTP200 responses rejected; two three-version series | `data/post_confirmation_v14/nih_clinvar/source_family_freeze.json` | Metadata intake; classifications/gold/evaluation pending |
| ClinVar engineering | Five tests pass,0skips,0.001s | `tests/test_clinvar_version_v14.py` | Prevents version/date relabeling; no clinical correctness claim |
| Formal ontology |112 asserted data triples; actual SHACL conforms; bounded closure887, no implemented-rule error | `reports/nih/ontology_v11_validation.json` | Partial formal checks; full OWL and unsatisfiable-class count unevaluated |
| Pathology v10 | OneFOV:46 vs42 count;F1 .431818;Dice .780564 | `reports/pathology/real_measurement_validation.md` | Quantity-specific demonstration; not patient generalization |
| Pathology v13 | Original failure; posthoc2/6fields from2/3participants;F1 .2921/.2951;count error39each | `reports/pathology/v13_recovery_results.md` | Four failures remain; full patient macro undefined |
| Documentary M2 | P1/P2/P3/P4 each1correct of2;rules/verifier samepair | `reports/decision/v13_m2_results.md` | No incremental verified-policy selection gain |
| Eligibility | User attestation; supporting records pending | `state/post_confirmation_resource_attestation.json` | No prize-eligibility conclusion |

Actual first-publication leakage is unknown, even where measured observed-availability violations are zero. Real contradiction-detection accuracy is not estimable without independent real positive cases. Synthetic attacks are labeled engineering evidence and are not added to the biomedical accuracy denominator. ClinVar and CIViC remain separate source families. Public access and reuse terms do not replace component-level eligibility review.
