# GO mapping contract v1

The original, standard-library implementation is `src/invariantcell/ontology_mapping.py`. It provides bounded GO OBO and GAF intake, typed JSON and attributed N-Triples export, a bridge to the existing temporal snapshot interface, and a finite `is_a` checker. It does not execute or import another project, fetch ontology imports, or download anything at runtime.

## Sources and pinned vocabulary

Primary specifications were consulted during this engineering session:

- [GO GAF 2.2 specification](https://geneontology.org/docs/go-annotation-file-gaf-format-2.2/) for columns, qualifiers, annotation dates, taxon identifiers, extensions and gene-product forms.
- [GO GAF 2.1 specification](https://geneontology.org/docs/go-annotation-file-gaf-format-2.1/) for legacy qualifier intake.
- [OBO format 1.4](https://owlcollab.github.io/oboformat/doc/GO.format.obo-1_4.html) for term tags, obsolete identifiers, replacement suggestions and unrecognized-tag preservation.
- [Biolink v4.3.6 primary schema](https://raw.githubusercontent.com/biolink/biolink-model/v4.3.6/biolink-model.yaml), read successfully as 455,362 bytes with SHA256 `37146d876aee83c1be58baf7da5a5bc97d6f3a5f5d5017484cf3b6cba0ece305`. The module pins this deliberately chosen version, without claiming it is the latest release.

Vocabulary spellings and mappings were checked against that schema. Live Biolink documentation was also inspected, but does not replace the pinned contract. No schema is bundled, and no runtime network request or LinkML validator is added.

| GAF relation | Export predicate | Exact mapping in pinned schema | Aspect |
| --- | --- | --- | --- |
| enables | biolink:enables | RO:0002327 | F |
| contributes_to | biolink:contributes_to | RO:0002326 | F |
| involved_in | biolink:actively_involved_in | RO:0002331 | P |
| located_in | biolink:located_in | RO:0001025 | C |
| active_in | biolink:active_in | RO:0002432 | C |
| part_of | biolink:part_of | BFO:0000050 | C |

GO namespaces map to `biolink:MolecularActivity`, `biolink:BiologicalProcess`, and `biolink:CellularComponent`. Supported subject categories are `biolink:Protein` and legacy `biolink:Gene`; a protein identifier is never converted to a gene identifier. Other object types remain retained but unsupported. Associations use the general verified `biolink:Association` category. In particular, the narrower `biolink:GeneToGoTermAssociation` is not imposed on proteins, because its pinned subject range is Gene.

## Intake and retention

`parse_obo(text, source_id, **source_times)` accepts a declared OBO 1.2 or 1.4 text snapshot. It models GO term ID/name/namespace, alternative IDs, plain `is_a`, `is_obsolete`, `replaced_by` and `consider`. It retains original tag values and non-Term stanzas. Ambiguous IDs, invalid booleans, unsupported modifiers on modeled ID tags and obsolete terms with modeled structural axioms are rejected. Annotations can resolve an `alt_id`, with the original annotated identifier retained. Obsolete annotations keep the original obsolete object and every replacement suggestion; they are excluded from temporal answers rather than reassigned.

`parse_gaf(text, source_id, **source_times)` checks a declared GAF 2.1/2.2 header, 17 columns, required column presence, GO ID/aspect, basic reference/form/taxon syntax, real calendar dates and qualifier cardinality. GAF 2.2 requires its generated-by and date-generated headers. All 17 original column strings, qualifier order, header lines, line numbers, annotation dates, evidence codes, taxon pairs, gene-product form IDs and extension/with-from expressions survive typed JSON export. Blank/NOT-only legacy GAF 2.1 qualifiers do not invent a relation: those records remain unmapped. Unknown 2.2 relation tokens are retained and excluded from temporal mapping.

The source checksum is SHA256 of the supplied text encoded as UTF-8. Callers ingesting a compressed or differently encoded file must separately retain its transport checksum; this interface does not claim to hash an unopened original file. IDs distinguish identical rows on different source lines. Parsing is intended for bounded text snapshots; it is not a streaming full-GO ingestion service.

This is a partial local shape contract, not complete GO validation. Database-prefix registries, evidence-code-specific field constraints, GO taxon constraints, all allowed product types, header-date grammars, general OBO escapes/modifiers and all GO relations are not exhaustively validated. Unknown content is data and never becomes an instruction.

## Time axes and scope

The GAF annotation date is a date describing annotation creation. It does not populate source publication/release time, ingestion time, event time, generation time, or biological validity.

`DocumentSource` takes explicit timezone-aware `public_available_at` and `ingested_at`, with unknown values retained as `None`. The GAF date-generated and OBO data-version headers remain source metadata; neither is substituted for those timestamps.

`to_temporal_snapshot(export, snapshot_id, context=..., ...)` returns a `TemporalMapping` with a snapshot and explicit excluded-record reasons. An active, supported mapping is necessary but insufficient for a temporal answer:

1. `state_kind_by_id` must explicitly classify each admitted annotation as `observed` or `predicted`. The adapter does not infer measurement status from an evidence code, including IEA. The caller must justify the interpretation of the attributed source assertion.
2. `validity_by_id` may supply a per-annotation `[from, to)` interval. Absence leaves validity unknown, and existing valid-at queries abstain. Explicit `(None, None)` asserts open bounds; it is never a default inferred from source dates.
3. The caller declares `mapping_available_at` for the pinned mapping contract and schema, and `mapping_ingested_at` for their local ingestion. Unknown availability blocks historical answers.
4. GAF, GO snapshot and mapping-contract dependencies must each pass the public-evidence cutoff. Mapped assertion ingestion is the latest of their declared ingestion times; any unknown dependency ingestion makes it unknown. Original ingestion times remain in the typed source records.
5. Context identity includes the caller context and a fingerprint of the exact GAF taxon/extension scope. Gene-product form IDs are subjects when supplied. Uninterpreted extensions are retained but excluded from this bridge; their AND/OR semantics are not flattened into broader assertions.

NOT maps to `polarity=refutes`; it never turns into a positive fact or a deletion of an earlier annotation. Opposite assertions of the same proposition remain a conflict. Incompatible taxa remain separate contexts. The evidence hash covers the complete mapped annotation, including raw columns and qualifiers. Source and mapping dependencies are carried into existing temporal certificates. Independent replay checks those certificates against an externally anchored snapshot and query, as in the existing system.

The source record, validity interval and state-kind declarations are caller assertions. The bridge does not establish source integrity, annotation truth, snapshot completeness, biological validity, or independence from a held-out evaluation.

## Formal export and check boundaries

`OntologyExport.to_dict()` is the complete typed, attributed local export: ontology terms/raw tags, GAF headers/columns, mapping issues, both source records and the pinned mapping contract. Standard JSON serialization gives a deterministic inspectable artifact; `canonical_hash()` gives its content fingerprint. No general JSON import/ontology round-trip parser is claimed.

`export_ntriples()` emits ontology classes, category identifiers, `rdfs:subClassOf`, typed obsolete flags, replacement/alternative-ID metadata, raw OBO tags, and reified attributed annotations. Annotation dates use `xsd:date`; explicit availability/ingestion times use `xsd:dateTime`; NOT uses `biolink:negated` with `xsd:boolean`. Positive and negative annotations are represented as attributed statements, not asserted bare subject-predicate-object edges. Unknown prefixes become lossless local CURIE URNs rather than guessed external identifier URLs. RDF is an interoperable syntactic projection; JSON is the full preservation format.

The module keeps three results separate:

- `check_ontology_shapes()` checks the local reference and active-parent namespace contract. It is not a SHACL result or proof of logical consistency.
- `check_ontology_logic()` passes only active plain `is_a` premises to the existing finite positive Horn engine. It derives transitive subclass paths, checks a GO DAG constraint and independently replays every listed derivation. A subclass cycle is a violation of this local GO DAG constraint; it is not reported as an OWL inconsistency. A finite closure budget defaults to 1,000 facts. Shape failures prevent this check from running.
- Full OWL consistency, unsatisfiable classes, SHACL conformance, complete LinkML/Biolink conformance and annotation entailment are `NOT_EVALUATED`. The export itself does not silently run logical checks. Imports, relationship rules, equivalent/intersection/union/disjoint axioms, and unmodeled stanzas are disclosed and not reasoned over. NOT annotations never enter positive Horn premises, and missing annotations never become negative facts.

## Executable engineering checks

Use the existing Python 3.12.3 environment without modifying it:

```bash
source ~/.venvs/lesegenv/bin/activate
PYTHONPATH=src python -B -m unittest discover -s tests -p test_ontology_mapping.py -v
PYTHONPATH=src python -B scripts/ontology_mapping_demo.py
PYTHONPATH=src python -B scripts/ontology_mapping_demo.py --format json
PYTHONPATH=src python -B scripts/ontology_mapping_demo.py --format ntriples
```

The demo uses original, explicitly synthetic fixtures. Default output reports shape checks, an `is_a` derivation/replay, unknown-validity abstention, a declared-fixture-validity conflict, future-ontology exclusion, and temporal replay. Alternative formats write only to stdout. No benchmark, real GO release, biomedical efficacy, clinical impact or NIH prize eligibility is established. NIH empirical resource eligibility remains `NIH_RESOURCE_ELIGIBILITY_UNRESOLVED`.
