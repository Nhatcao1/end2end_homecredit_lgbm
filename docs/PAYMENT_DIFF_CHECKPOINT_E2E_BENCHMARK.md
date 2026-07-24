# Exact checkpoint PAYMENT_DIFF E2E benchmark

This benchmark uses an external timing probe to import and invoke
`code/heir/examples/payment_diff_checkpoint_e2e.py` as its HE workload. The
application example contains no timers, and the probe does not maintain a
second copy of the encrypted feature logic.

The external probe records the cold path:

1. post-PSI client layout;
2. public CKKS scale selection;
3. separate HEIR SUM, MEAN, and VAR compile/setup/encrypt/evaluate/checkpoint
   stages;
4. source-built OpenFHE CKKS-to-FHEW MAX setup and evaluation;
5. final isolated audit decrypts;
6. final feature CSV creation.

The preferred simple mode does not use PSI. The client explicitly allows one
`SK_ID_CURR`, scans the clean installments table, preserves the complete group
in stable source order, and writes one fixed-width private CSV:

```text
SK_ID_CURR, opaque_group_id, lane, source_row,
AMT_INSTALMENT, AMT_PAYMENT, VALID_MASK
```

Real rows occupy the first lanes with `VALID_MASK=1`; numeric-zero padding
lanes have `VALID_MASK=0`. The preparation never truncates or splits a group.
If the complete clean group exceeds `--bucket-size`, it writes
`client_preparation.json` with status `HE_UNSUPPORTED_COMPLETE_GROUP` and does
not launch HE.

The mask is structural in this first implementation: mask-one rows become the
real encrypted parent lanes, mask-zero rows become the HEIR packer's zero
padding, and the public valid count fixes the aggregate reduction boundary.
It is not yet a separately encrypted mask/count circuit.

The benchmark runs the equivalent Pandas expression over the same mask-one
rows:

```python
frame["PAYMENT_DIFF"] = (
    frame["AMT_INSTALMENT"] - frame["AMT_PAYMENT"]
)
frame.groupby("opaque_group_id")["PAYMENT_DIFF"].agg(
    ["max", "mean", "sum", "var"]
)
```

It reports client preparation, cold HE latency, latency for every output
branch, Pandas latency, and final absolute/relative error.

Run:

```bash
python3 code/heir/scripts/run_payment_diff_checkpoint_e2e_benchmark.py \
  --installments data/home_credit/installments_payments.csv \
  --allowed-sk-id-curr 100001 \
  --bucket-size 128 \
  --max-ring-dimension 16384 \
  --openfhe-dir /usr/local/lib/OpenFHE \
  --relative-tolerance 1e-5 \
  --output-dir benchmark_runs/payment_diff_checkpoint_e2e_benchmark_01 \
  --overwrite
```

The run is intentionally fresh. Do not pass the example's
`--resume-checkpoints` option when collecting cold benchmark latency.
