# Synthetic VND CT+CT magnitude benchmark

This is a small standalone arithmetic benchmark. It has no relationship to
PAYMENT_DIFF, installments, groupby, PSI, or the credit model.

## Prepare the environment

```bash
cd /root/end2end_homecredit_lgbm
source .venv-openfhe/bin/activate
python3 -m pip install numpy
python3 -c "import openfhe; print(openfhe.__file__)"
```

## Generate deterministic data

```bash
python3 -m code.openfhe_direct.benchmarks.synthetic_vnd.generate_dataset \
  --output-dir data/generated/vnd_ct_add \
  --value-count 50 100 2000 \
  --minimum-value 100000 \
  --maximum-value 200000000 \
  --seed 20260731 \
  --overwrite
```

Every file contains `INDEX`, `LEFT_VALUE`, and `RIGHT_VALUE`: two aligned
numeric vectors, A and B. Smaller vectors are exact prefixes of the largest
generated sequence.

## Run CT+CT only

```bash
python3 -m code.openfhe_direct.benchmarks.synthetic_vnd.add \
  --dataset-dir data/generated/vnd_ct_add \
  --value-count 50 100 2000 \
  --slot-count 8192 \
  --repetitions 5 \
  --multiplicative-depth 2 \
  --scaling-mod-size 50 \
  --first-mod-size 60 \
  --ring-dimension 16384 \
  --output-dir benchmark_runs/openfhe_synthetic_vnd_ct_add \
  --overwrite
```

The benchmark calls only `OpenFHECreditSession.encrypt()`, `add()`, and
`decrypt()`. NumPy `float64` vector addition is the plaintext reference because
it measures the optimized numeric operation without Pandas Series/index
overhead. Each vector-length directory contains a compact `REPORT.md`, raw
`results.csv`, and `summary.json`.
