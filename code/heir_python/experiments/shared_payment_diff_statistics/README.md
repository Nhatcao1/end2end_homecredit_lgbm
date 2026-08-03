# Shared PAYMENT_DIFF statistics experiment

This is capability-test code, not a benchmark. One HEIR-Python CKKS program:

1. encrypts aligned `AMT_INSTALMENT` and `AMT_PAYMENT` parents once;
2. calculates encrypted `PAYMENT_DIFF = AMT_INSTALMENT - AMT_PAYMENT`;
3. shares that derived ciphertext across SUM, MEAN, and sample VARIANCE;
4. returns one encrypted `tensor<3xf64>`;
5. decrypts only that final tensor for review.

```text
AMT_INSTALMENT CT ─┐
                   ├─ CT subtraction ─ PAYMENT_DIFF CT ─┬─ SUM ─────┐
AMT_PAYMENT CT ────┘                                    ├─ MEAN ────┼─ encrypted tensor<3>
                                                       └─ VARIANCE ┘
```

Run from the repository root:

```bash
source .venv-heir/bin/activate

python3 -m code.heir_python.experiments.shared_payment_diff_statistics.run \
  --input data/prepared/examples/payment_diff_demo_group.csv \
  --width 8 \
  --input-scale 2048 \
  --debug
```

Expected plaintext values for the committed fixture are SUM `60`, MEAN `20`,
and sample VARIANCE `17200`.

The experiment may expose a current HEIR-Python lowering limitation around the
single tensor result. Failure is kept isolated here and does not change the
stable scalar-per-program session API.
