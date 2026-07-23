# Exact checkpoint PAYMENT_DIFF E2E benchmark

This benchmark executes
`code/heir/examples/payment_diff_checkpoint_e2e.py` as its HE workload. It
does not maintain a second copy of the encrypted feature logic.

The example's optional `--execution-json` trace records the cold path:

1. post-PSI client layout;
2. public CKKS scale selection;
3. separate HEIR SUM, MEAN, and VAR compile/setup/encrypt/evaluate/checkpoint
   stages;
4. source-built OpenFHE CKKS-to-FHEW MAX setup and evaluation;
5. final isolated audit decrypts;
6. final feature CSV creation.

The benchmark then prepares the same deterministic post-PSI applicant group
and runs the equivalent Pandas expression:

```python
frame["PAYMENT_DIFF"] = (
    frame["AMT_INSTALMENT"] - frame["AMT_PAYMENT"]
)
frame.groupby("opaque_group_id")["PAYMENT_DIFF"].agg(
    ["max", "mean", "sum", "var"]
)
```

It reports cold end-to-end latency, latency for every output branch, Pandas
latency, and final absolute/relative error. SecretFlow PSI protocol execution
is excluded because the example starts from an existing PSI bridge. Reading
that bridge, scanning installments, selecting a group, and padding are
included.

Run:

```bash
python3 code/heir/scripts/run_payment_diff_checkpoint_e2e_benchmark.py \
  --installments data/home_credit/installments_payments.csv \
  --bridge-dir benchmark_runs/psi/installments_application/rr22_train_test_01 \
  --bucket-size 128 \
  --max-ring-dimension 16384 \
  --openfhe-dir /usr/local/lib/OpenFHE \
  --relative-tolerance 1e-5 \
  --output-dir benchmark_runs/payment_diff_checkpoint_e2e_benchmark_01 \
  --overwrite
```

The run is intentionally fresh. Do not pass the example's
`--resume-checkpoints` option when collecting cold benchmark latency.
