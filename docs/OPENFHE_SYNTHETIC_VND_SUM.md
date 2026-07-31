# Synthetic VND BGV SUM benchmark

This standalone benchmark reduces one encrypted integer vector to one exact
encrypted total. It has no relationship to PAYMENT_DIFF, groupby, PSI, or the
credit model.

## Prepare the environment

```bash
cd /root/end2end_homecredit_lgbm
source .venv-openfhe/bin/activate
python3 -m pip install numpy
python3 -c "import openfhe; print(openfhe.__file__)"
```

## Generate deterministic VND vectors

```bash
python3 -m code.openfhe_direct.benchmarks.synthetic_vnd.generate_dataset \
  --output-dir data/generated/vnd_sum \
  --value-count 50 100 2000 \
  --minimum-value 100000 \
  --maximum-value 20000000 \
  --seed 20260731 \
  --overwrite
```

Every file contains one `VALUE` vector. The endpoints are generation
boundaries; actual values are deterministic random integers throughout the
interval. Smaller vectors are exact prefixes of the largest vector.

## Run encrypted BGV SUM

```bash
python3 -m code.openfhe_direct.benchmarks.synthetic_vnd.sum \
  --dataset-dir data/generated/vnd_sum \
  --value-count 50 100 2000 \
  --slot-count 8192 \
  --repetitions 5 \
  --multiplicative-depth 1 \
  --plaintext-modulus 100000038913 \
  --ring-dimension 16384 \
  --output-dir benchmark_runs/openfhe_vnd_bgv_sum \
  --overwrite
```

The benchmark calls only `OpenFHEBgvSession.encrypt()`, `sum()`, and
`decrypt()`. NumPy `int64` SUM is the optimized plaintext reference. The
configured 37-bit modulus has positive centered capacity `50000019456`, safely
above the worst-case 2,000-value total of `40000000000`. Unsafe combinations
are rejected before encryption rather than allowed to wrap modulo the
plaintext modulus.

Each vector-length directory contains `REPORT.md`, `results.csv`, and
`summary.json`. The report includes the actual plaintext total, BGV audit
error, setup, encryption, encrypted reduction, and audit-decryption latency.
