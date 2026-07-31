# Prepared PAYMENT_DIFF demo group

`payment_diff_demo_group.csv` is a synthetic, non-production fixture for the
checkpoint E2E example and benchmark. It contains:

- three valid parent-column lanes;
- one zero-padding lane;
- a contiguous `VALID_MASK` of `1,1,1,0`;
- a synthetic applicant identifier, `DEMO_001`.

It does not come from the Home Credit dataset and contains no real applicant
identifier. The expected plaintext `PAYMENT_DIFF` values are `160`, `-100`,
and `0`.

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
