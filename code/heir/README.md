# HEIR benchmark code

The implementation has two layers:

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
workloads/
├── catalog.py                 registry of 13 function benchmark tasks
├── grouped.py                 workload/feature contracts
├── bureau_and_balance/        B01-B03 definitions
├── previous_applications/     P01-P03 definitions
├── pos_cash/                  POS01-POS02 definitions
├── installments_payments/     I01-I03 definitions
└── credit_card_balance/       C01-C02 definitions
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

Prepare all 13 function-specific reports over the shared kernels:

```bash
python3 code/heir/scripts/run_function_benchmarks.py \
  --task all \
  --application-row-limit 8 \
  --run-name review_v1
```

Use `--task B02`, for example, to prepare only the active-bureau branch. Each
task writes its own `benchmark_report.md`, source-facing
`plaintext_reference.csv`, raw `kernel_oracle.csv`, and tensor manifest under
`benchmark_runs/functions/<function>/<task>/<run-name>/`.

The shared preparation engine performs grouping, missing-value compaction,
ratio/clipping policy, branch-mask construction, fixed-shape padding, and
client-private identifier mapping. These are trusted client operations; only
the referenced K01-K03 arithmetic is intended for HEIR execution. Prepared
tensor CSVs are plaintext staging artifacts and must be encrypted before they
leave the client.

Prepare the tensors and Markdown report without claiming HE execution:

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
