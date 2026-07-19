# HEIR benchmark code

The first workload reconstructs the original `POS_COUNT` feature with an
anonymous padded history mask and a HEIR-generated CKKS dot product.

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
