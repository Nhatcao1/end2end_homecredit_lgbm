# Shared PAYMENT_DIFF statistics experiment

This is capability-test code, not a benchmark. One HEIR-Python CKKS program:

1. encrypts aligned `AMT_INSTALMENT` and `AMT_PAYMENT` parents once;
2. calculates encrypted `PAYMENT_DIFF = AMT_INSTALMENT - AMT_PAYMENT`;
3. reuses the same parent ciphertexts and context for three evaluations;
4. selects SUM, MEAN, or sample VARIANCE using public one-hot weights;
5. decrypts only the three final scalar ciphertexts for review.

```text
AMT_INSTALMENT CT ─┐
                   ├─ same HEIR program ─┬─ select SUM ────── SUM CT
AMT_PAYMENT CT ────┘                     ├─ select MEAN ───── MEAN CT
                                         └─ select VARIANCE ─ VARIANCE CT
```

Run from the repository root:

```bash
source .venv-heir/bin/activate

python3 -m code.heir_python.experiments.shared_payment_diff_statistics.run \
  --input data/prepared/examples/payment_diff_demo_group.csv \
  --width 8 \
  --input-scale 2048
```

Do not add `--debug` with HEIR-Python 2026.7.1 for this circuit. That option
adds `--view-op-graph`, whose graph-generation path crashes in `heir-opt`.

Expected plaintext values for the committed fixture are SUM `60`, MEAN `20`,
and sample VARIANCE `17200`.

HEIR-Python currently exposes one result decryptor (`result0`). Returning a
`tensor<3xf64>` failed after secret lowering because each encrypted scalar had
already become a packed ciphertext tensor. This experiment therefore keeps one
program/context and one parent encryption, but performs three evaluations.
