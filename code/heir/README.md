# HEIR benchmark code

Workloads are organized under the original functions from
`lightgbm_with_simple_features.py`. Each function package contains only the
sub-operations selected for HEIR; client-only and excluded operations are
tracked in `docs/HEIR_BENCHMARK_CRITERIA.md` without placeholder code.

The first workload sits under `workloads/pos_cash/` and reconstructs the
original `POS_COUNT` feature with an anonymous padded history mask and a
HEIR-generated CKKS dot product.

```text
workloads/
└── pos_cash/
    └── pos_count.py
```

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
