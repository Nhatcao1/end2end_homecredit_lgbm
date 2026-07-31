# Direct OpenFHE-Python API benchmark

This benchmark calls the public methods on `OpenFHECreditSession` directly.
It does not use HEIR, generated C++, CMake, a gateway, or an HTTP client.

The timed functions are:

```text
encrypt                    decrypt
add                        subtract
multiply                   add_public_scalar
add_public_vector          multiply_public_scalar
multiply_public_vector     square
sum                        mean
variance_components        covariance_components
correlation_components     weighted_sum
risk_score
```

One local CKKS context and key set are created. The two parent columns are
encrypted once. `PAYMENT_DIFF` is produced as ciphertext subtraction and is
the encrypted input for the aggregate methods.

The report intentionally stays small:

| Result | Meaning |
|---|---|
| Setup seconds | One context and key-generation cost |
| Parent encryption seconds | Encrypt both input columns |
| Median method latency | Only the named API call |
| Error/status | Final decryption used for correctness audit |

Audit decryption is excluded from method latency.

## Run

```bash
source .venv-heir-python/bin/activate

python3 code/openfhe_direct/benchmark.py \
  --prepared-group data/prepared/examples/payment_diff_demo_group.csv \
  --repetitions 5 \
  --multiplicative-depth 4 \
  --output-dir benchmark_runs/openfhe_direct_api \
  --overwrite
```

The previous command path remains as a compatibility entry point:

```bash
python3 code/heir/scripts/run_python_arithmetic_backend_benchmark.py \
  --prepared-group data/prepared/examples/payment_diff_demo_group.csv \
  --repetitions 5 \
  --multiplicative-depth 4 \
  --output-dir benchmark_runs/openfhe_direct_api \
  --overwrite
```

Artifacts:

```text
results.csv
summary.json
REPORT.md
```
