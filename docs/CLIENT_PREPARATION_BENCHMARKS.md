# Client preparation benchmarks

PSI input construction and grouped fixed-block construction are data-owner
preparation stages. They can be measured without HEIR, OpenFHE, CKKS keys, or
the feature-calculation benchmarks.

## Boundaries

| Benchmark | Measured | Explicitly excluded |
|---|---|---|
| PSI input preparation | Read identifier columns, validate receiver uniqueness, deduplicate and union sender identifiers, sort and write party input CSVs | SecretFlow protocol/network time, PSI bridge, grouping, encryption |
| Group preparation | Read a completed PSI intersection, scan installments, partition/sort by `SK_ID_CURR`, assign opaque groups, write parent shards and fixed-block padding layout | PSI protocol time, `PAYMENT_DIFF`, aggregation, encryption, HEIR/OpenFHE |

The two benchmarks are independent. PSI preparation may be rerun without group
preparation. Group preparation may consume an already-existing completed PSI
output without rerunning PSI.

## Output

PSI preparation writes its timing data inside `psi_input_manifest.json` and a
small `PSI_PREPARATION_BENCHMARK.md`.

Group preparation writes `group_preparation_report.json` and
`GROUP_PREPARATION_BENCHMARK.md`. The large private parent-row artifacts are
expected for a full-data run and remain ignored by Git.
