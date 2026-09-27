# PhenotypeMeasurement contract

The implemented contract is [phenotype.py](../src/invariantcell/phenotype.py). It records measurements and evidence status; it does not establish biological accuracy. Current behavior is demonstrated in [receipt](../demo/pathology/receipt.json), with [validity limits](../reports/pathology/measurement_validity.md).

| Field group | Implemented representation |
|---|---|
| Identity/context | Required measurement, subject, context, organism and assay identifiers |
| Source integrity | `source_asset_ref`, SHA-256; annotation reference/hash/version all present together or all absent |
| Features | Name, finite value, unit, quantity (`area`, `count`, `pixel`, `embedding`, `density`, `morphology`), quantity-specific support (`supported`, `unsupported`, `unknown`) |
| Regions | Unique ID, parent ID and region type; dangling parents and cycles rejected |
| Method | `measurement_method`, `measurement_version` |
| Evidence type | `OBSERVED`, `PREDICTED`, `INFERRED`, `SYNTHETIC` |
| Quality | Nonnegative uncertainty or null, flags, limitations |
| Time | Separate `observed_at`, `public_available_at`, `ingested_at`; unknown permitted |

An annotation update creates a new measurement ID/version and retains old hashes. `as_of` requires known public-availability and ingestion times at or before both cutoffs; duplicate measurement IDs are rejected. It returns all admitted versions rather than silently selecting the newest. Explicit supersession relations and biological validity intervals are not implemented by this helper. Annotation/publication time does not replace `observed_at`.

`PathologyImageAdapter.measure` checks co-registration and content agreement against a trusted manifest. The certificate records measurement and manifest digests; replay recomputes against an independently supplied expected manifest hash. Callers must establish that trust anchor. Automatic annotation of an observed image becomes `INFERRED`. Label arithmetic alone proves neither physical area nor biological object identity.

`measurement_decision(record, quantity)` abstains when the requested quantity lacks supported features, defers derived/synthetic records, and otherwise returns eligibility with no biological-benefit claim. Support flags are declarations. Hierarchy checks validate graph structure, not geometric containment.

`measurement_snapshot` creates shared `EvidenceRecord`, `DependencyRef` and `Snapshot` objects. Only `OBSERVED` becomes observed evidence; all other kinds become predicted evidence, with synthetic status retained. The source measurement retains the predicted/inferred distinction. Unknown dates/validity can exclude an answer even when predictions are allowed; a valid replay can certify that exclusion.

Implemented measurements are bounded mask fractions/counts and one real embedding norm. Broader morphology, densities, nested biological compartments, spatial containment, calibrated uncertainty, validated segmentation, unit ontologies and a matched molecular bridge remain pending.

P09 human histopathology, maize leaf phenotypes and molecular observables require distinct organisms, contexts, assays and units. Shared fields do not make them interchangeable. Arc response prediction has no pathology dependency. A molecular bridge needs matched intervention, construct, cellular context, assay/time and independently measured endpoints; identifier agreement alone is insufficient.

Next example: `source ~/.venvs/lesegenv/bin/activate && PYTHONPATH=src python -B scripts/pathology_demo.py`.
