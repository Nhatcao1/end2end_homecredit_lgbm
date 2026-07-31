# HEIR and OpenFHE Python setup

## Decision

Canonical Python benchmark routing is:

| Calculation | Backend |
|---|---|
| ADD, SUBTRACT, MULTIPLY | HEIR |
| SUM, COUNT, MEAN, SQUARE-SUM, VARIANCE | HEIR |
| WEIGHTED-SUM, DOT-PRODUCT, POLYNOMIAL | HEIR |
| MINIMUM, MAXIMUM, encrypted comparison | OpenFHE Python |

Use HEIR whenever the operation can be expressed by the compiled HEIR CKKS
programs. OpenFHE Python is only the fallback for CKKS↔FHEW scheme switching.

The policy is implemented in:

```text
code/heir/python_api/backend_policy.py
```

## Why this uses a separate virtual environment

The server also has a source-built OpenFHE installation:

```text
/usr/local/lib/OpenFHE/OpenFHEConfig.cmake
```

The PyPI `openfhe` package contains its own Python/native runtime built for a
specific Ubuntu release. It is not a Python binding for the arbitrary
development build under `/usr/local`.

Keep the official Python packages in `.venv-heir-python`. Do not add
`/usr/local/lib/OpenFHE` to `LD_LIBRARY_PATH` while using this environment.
This avoids loading two potentially incompatible OpenFHE ABIs in one process.

## Install

From the repository root:

```bash
chmod +x scripts/setup_heir_openfhe_python.sh
./scripts/setup_heir_openfhe_python.sh
source .venv-heir-python/bin/activate
```

The setup script currently pins:

```text
heir_py[python,openfhe] == 2026.7.1
openfhe                 == 1.5.1.0.24.4 on Ubuntu 24.04
openfhe                 == 1.5.1.0.22.4 on Ubuntu 22.04
```

Both OpenFHE wheels require Python 3.12 for this project setup.
The script also installs `pandas>=2.2,<4` for the benchmark's equivalent
plaintext workload.

Override the environment location if needed:

```bash
HEIR_OPENFHE_VENV=/root/venvs/heir-openfhe-python \
  ./scripts/setup_heir_openfhe_python.sh
```

## Verify

```bash
python3 code/heir/scripts/check_heir_openfhe_python.py \
  --expected-heir 2026.7.1 \
  --expected-openfhe 1.5.1.0.24.4
```

The output includes the package versions and the approved backend route for
each calculation.

## Canonical benchmarks

Primitive HEIR versus optional OpenFHE-Python comparison:

```bash
python3 code/heir/scripts/run_python_arithmetic_backend_benchmark.py \
  --backend both \
  --value-count 1000 \
  --slot-count 1024 \
  --ring-dimension 16384 \
  --repetitions 1 \
  --output-dir benchmark_runs/python_arithmetic_smoke \
  --overwrite
```

Its full metric contract and larger command are documented in
[`PYTHON_ARITHMETIC_BACKEND_BENCHMARK.md`](PYTHON_ARITHMETIC_BACKEND_BENCHMARK.md).

Small independent routing proof:

```bash
python3 code/heir/scripts/run_official_python_var_minmax_trial.py \
  --values 160 -100 0 60 250 \
  --width 8 \
  --repetitions 1 \
  --ring-dimension 16384 \
  --output-dir benchmark_runs/python_heir_openfhe_var_minmax \
  --overwrite
```

This runs VAR with HEIR and MIN/MAX with OpenFHE Python.

Checkpointed PAYMENT_DIFF benchmark:

```bash
python3 code/heir/scripts/run_payment_diff_checkpoint_e2e_benchmark.py \
  --installments data/home_credit/installments_payments.csv \
  --allowed-sk-id-curr 100001 \
  --max-ring-dimension 16384 \
  --relative-tolerance 1e-5 \
  --output-dir benchmark_runs/payment_diff_openfhe_python_100001 \
  --overwrite
```

Its MAX branch stores the completed OpenFHE-Python context/key/ciphertext
bundle. It does not invoke CMake or require `--openfhe-dir`.

Post-PSI PAYMENT_DIFF proof:

```bash
python3 code/heir/scripts/run_official_python_payment_diff_e2e.py \
  --installments data/home_credit/installments_payments.csv \
  --bridge-dir benchmark_runs/psi/installments_application/rr22_train_test_01 \
  --group-count 2 \
  --bucket-size 128 \
  --output-dir benchmark_runs/python_heir_openfhe_payment_diff \
  --overwrite
```

This runs SUBTRACT, SUM, MEAN, and VARIANCE with HEIR. MAXIMUM uses a separate
OpenFHE Python context because the two runtimes do not expose a ciphertext
interchange contract.

## References

- HEIR installation: <https://heir.dev/docs/getting_started/>
- HEIR OpenFHE backend: <https://heir.dev/docs/dialects/openfhe/>
- OpenFHE Python package: <https://pypi.org/project/openfhe/>
