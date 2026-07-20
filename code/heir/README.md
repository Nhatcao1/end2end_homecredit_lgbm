# HEIR benchmark code

## Active simplified direction

New development begins with `operations/`, not whole-function DAGs. These are
function-agnostic encrypted-column expressions. Each one has an explicit
capability route, an MLIR builder or a deferred status, and a benchmark timing
contract that compares Python calculation time only with encrypted evaluation
time. Encryption and decryption are recorded separately for operational and
accuracy evidence.

See `docs/GENERIC_ENCRYPTED_OPERATIONS.md` before adding a workload adapter.
Only null/value-mask packing is permitted before encryption; a source feature
expression must be evaluated after encryption or reported as not implemented.

The first compact source benchmark is `installments_payment_diff`, the exact
two-column subtraction from `installments_payments()`. It has its own
generation and execution commands in that document. It does not require the
historical multi-stage DAG. Ratio and comparison source expressions are
deliberately emitted as deferred reports until their approximate/scheme-switch
routes are separately implemented and benchmarked.

## Historical function adapters

The implementation also has the following historical layers:

- `kernels/` contains reusable HEIR arithmetic independent of Home Credit.
- `workloads/` contains preparation and benchmarks organized under the
  original functions from `lightgbm_with_simple_features.py`.

Each function package contains only sub-operations selected for HEIR;
client-only and excluded operations are tracked in
`docs/HEIR_BENCHMARK_CRITERIA.md` without placeholder code. Different function
reports may reference the same generated kernel source and SHA256 hash.

Linear and polynomial scoring are implemented in a separate `Special /
non-source` experiment lane. They must not be presented as original pipeline
operations or counted toward function-parity coverage. Tiny exported-tree
inference remains documented but intentionally has no code.

The first workload sits under `workloads/pos_cash/` and reconstructs the
original `POS_COUNT` feature with an anonymous padded history mask and a
HEIR-generated CKKS dot product.

```text
kernels/
├── contracts.py               serializable kernel contract
├── registry.py                all active non-tree kernels
├── dot_product.py             K01 encrypted dot product
├── moments.py                 K02 masked sufficient statistics
├── difference_moments.py      K03 difference statistics
├── linear_score.py            S01 special linear score
└── polynomial_score.py        S02 special polynomial transform
operations/
├── contracts.py               explicit exact/approximate/switching capability matrix
├── columns.py                 generic encrypted add/subtract/multiply MLIR
└── benchmarking.py            separate calculation/encryption/decryption timing
workloads/
├── catalog.py                 registry of 5 complete function benchmarks
├── grouped.py                 function/component/feature contracts
├── bureau_and_balance/        general/active/closed components
├── previous_applications/     general/approved/refused components
├── pos_cash/                  count/numeric components
├── installments_payments/     count/prepared/difference components
└── credit_card_balance/       count/numeric components
backends/
└── generated_ckks.py         strict generated-source execution
scripts/
├── prepare_reusable_kernels.py
├── run_function_benchmarks.py
└── run_pos_count_benchmark.py
```

Emit reviewable MLIR, source hashes, contracts, and deterministic plaintext
oracles for every reusable arithmetic kernel:

```bash
python3 code/heir/scripts/prepare_reusable_kernels.py \
  --output-dir benchmark_runs/reusable_kernels/arithmetic_layer_v1 \
  --vector-size 8 \
  --polynomial-degree 3
```

This command prepares benchmark inputs but does not claim encrypted execution.
The manifest records `mlir_and_plaintext_oracle_only` until HEIR-generated CKKS
source is compiled and run. K01 already has a strict generated-source runner;
the remaining generated runners are the next layer of work.

Prepare one complete function report over the shared kernels:

```bash
python3 code/heir/scripts/run_function_benchmarks.py \
  --function bureau \
  --application-row-limit 8 \
  --source-row-limit 500000 \
  --run-name bureau_smoke
```

The public choices are `bureau`, `previous`, `pos`, `installments`, and
`credit_card`. Internal trace IDs such as B01/B02/B03 remain only to map report
sections back to source operations; they are not independently runnable
benchmarks. Each function writes one `benchmark_report.md`, consolidated
`plaintext_reference.csv`, `kernel_oracle.csv`, `tensor_manifest.csv`, and
`feature_bundle_manifest.json` under
`benchmark_runs/functions/<function>/<run-name>/`.

The shared preparation engine performs grouping, missing-value compaction,
ratio/clipping policy, branch-mask construction, fixed-shape padding, and
client-private identifier mapping. These are trusted client operations; only
the referenced K01-K03 arithmetic is intended for HEIR execution. Prepared
tensor CSVs are plaintext staging artifacts and must be encrypted before they
leave the client. The bundle manifest is currently
`plaintext_staging_only`: its ciphertext list remains empty until a generated
CKKS backend returns and serializes ciphertext outputs.

For multi-owner experiments, `code/bridge/psi_to_heir.py` writes a dense sender
application layout containing matching `SK_ID_CURR` values and blank rows for
receiver-only applicants. Passing that file through `--application` preserves
receiver-left-join shape without exposing TARGET or unmatched identifiers to
the sender. Blank identifiers are intentional zero-history slots.

The older POS_COUNT-only command remains as a narrow K01 generated-runner test,
not the public function-benchmark interface:

```bash
python3 code/heir/scripts/run_pos_count_benchmark.py \
  --application-row-limit 8 \
  --run-name pos_count_prepare_8
```

Run the strict generated-source backend when CKKS `heir_output.cpp/h` is
available:

```bash
python3 code/heir/scripts/run_pos_count_benchmark.py \
  --backend heir-generated-ckks \
  --heir-generated-dir /path/to/heir-generated-ckks \
  --openfhe-dir /path/to/openfhe/lib/OpenFHE \
  --heir-vector-size 8192
```

The backend rejects BGV, BFV, and BinFHE generated source. Benchmark outputs
are written under `benchmark_runs/` and remain local because they can become
large.
