# BGV and CKKS multiplication-limit probe

This small probe answers two separate questions without chaining results:

```text
10 × 0.1
100 × 0.1
1,000 × 0.1
...
10^15 × 0.1

10 × 10
100 × 100
1,000 × 1,000
...
10^15 × 10^15
```

Every row starts from fresh plaintext operands, encrypts them independently,
performs exactly one ciphertext-ciphertext multiplication, and decrypts only
for the final audit. A result from one magnitude is never used by another.

## Why the schemes are reported differently

BGV accepts integers and calculates exactly modulo its plaintext modulus.
For the `× 0.1` lane, the client uses fixed-point encoding: `0.1` becomes the
integer `1`, and the final audited value is divided by the public scale `10`.
The report distinguishes an exact ordinary result from an exact modular
wraparound and from an input rejected by the configured plaintext space.

CKKS accepts real values and is approximate. The report records both absolute
and relative error. An absolute error over one unit at a very large magnitude
does not automatically mean the relative result is unusable.

## Run

```bash
cd /root/end2end_homecredit_lgbm
source .venv-openfhe/bin/activate

python3 -m code.openfhe_direct.benchmarks.synthetic_numeric.multiplication_limits \
  --repetitions 1 \
  --output-dir benchmark_runs/openfhe_multiplication_limits \
  --overwrite
```

Artifacts:

```text
benchmark_runs/openfhe_multiplication_limits/
├── REPORT.md
├── results.csv
└── summary.json
```

`summary.json` records the first observed magnitude where BGV stops matching
ordinary arithmetic, CKKS absolute error exceeds one, CKKS relative error
exceeds `1e-6`, or CKKS rejects evaluation. Cryptographic parameters are fixed
in `code/openfhe_direct/profiles.py`, not exposed as benchmark arguments.
