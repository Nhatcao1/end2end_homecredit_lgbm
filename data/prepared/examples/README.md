# Prepared PAYMENT_DIFF demo group

This directory contains synthetic, non-production fixtures for the checkpoint
E2E example and benchmark:

| File | Synthetic ID | Real lanes | Packed width | Padding lanes |
|---|---|---:|---:|---:|
| `payment_diff_demo_group.csv` | `DEMO_001` | 3 | 4 | 1 |
| `payment_diff_demo_group_002.csv` | `DEMO_002` | 3 | 4 | 1 |
| `payment_diff_demo_group_003.csv` | `DEMO_003` | 5 | 8 | 3 |
| `payment_diff_demo_group_004.csv` | `DEMO_004` | 8 | 8 | 0 |
| `payment_diff_demo_group_005.csv` | `DEMO_005` | 10 | 16 | 6 |

They do not come from the Home Credit dataset and contain no real applicant
identifiers. Every file uses contiguous real lanes followed by numeric-zero
padding and a matching `VALID_MASK`.

Run the complete benchmark from a fresh clone:

```bash
source .venv-heir-python/bin/activate

python3 code/heir/scripts/run_payment_diff_checkpoint_e2e_benchmark.py \
  --prepared-group data/prepared/examples/payment_diff_demo_group.csv \
  --max-ring-dimension 16384 \
  --relative-tolerance 1e-5 \
  --output-dir benchmark_runs/payment_diff_prepared_demo \
  --overwrite
```

All other files under `data/` remain ignored.

Run every fixture sequentially:

```bash
for prepared_group in data/prepared/examples/payment_diff_demo_group*.csv; do
  run_name="$(basename "${prepared_group}" .csv)"
  python3 code/heir/scripts/run_payment_diff_checkpoint_e2e_benchmark.py \
    --prepared-group "${prepared_group}" \
    --max-ring-dimension 16384 \
    --relative-tolerance 1e-5 \
    --output-dir "benchmark_runs/${run_name}" \
    --overwrite || break
done
```
