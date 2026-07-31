# Synthetic VND SUM benchmarks

These standalone benchmarks reduce one generated VND vector with exact BGV or
approximate CKKS. They have no relationship to PAYMENT_DIFF, groupby, PSI, or
the credit model.

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

## Run exact BGV SUM

```bash
python3 -m code.openfhe_direct.benchmarks.synthetic_vnd.sum \
  --dataset-dir data/generated/vnd_sum \
  --value-count 50 100 2000 \
  --slot-count 2048 \
  --repetitions 5 \
  --multiplicative-depth 0 \
  --plaintext-modulus-bits 40 \
  --ring-dimension 16384 \
  --output-dir benchmark_runs/openfhe_vnd_bgv_sum \
  --overwrite
```

The command specifies only a bit budget. The benchmark selects a compatible
packed-plaintext prime internally and records the actual modulus in the
report. It calls only `OpenFHEBgvSession.encrypt()`, `sum()`, and `decrypt()`.
NumPy `int64` SUM is the plaintext reference. Unsafe totals are rejected before
encryption rather than allowed to wrap modulo the plaintext modulus.

The 40-bit number is the BGV plaintext space, not an OpenFHE RNS-prime size.
SUM has no ciphertext multiplication, so the session requests depth `0` and
uses `FIXEDMANUAL` with one compatible 60-bit first RNS prime. It creates no
unnecessary multiplication level and stays within OpenFHE's 60-bit limit.

## Run approximate CKKS SUM on the same vectors

```bash
python3 -m code.openfhe_direct.benchmarks.synthetic_vnd.ckks_sum \
  --dataset-dir data/generated/vnd_sum \
  --value-count 50 100 2000 \
  --slot-count 2048 \
  --repetitions 5 \
  --normalization-divisor 10000 \
  --multiplicative-depth 2 \
  --scaling-mod-size 50 \
  --first-mod-size 60 \
  --ring-dimension 16384 \
  --absolute-tolerance-vnd 1 \
  --relative-tolerance 1e-6 \
  --output-dir benchmark_runs/openfhe_vnd_ckks_sum \
  --overwrite
```

CKKS divides each VND value by `10000` before encryption, computes the SUM on
ciphertext, and multiplies the final decrypted audit scalar by `10000`. The
encrypted calculation never sees the restored large VND values.

Each scheme and vector-length directory contains `REPORT.md`, `results.csv`,
and `summary.json`. Reports include the actual plaintext total, audit error,
setup, encryption, encrypted reduction, and audit-decryption latency.
