# Direct OpenFHE-Python API benchmark

This benchmark uses a client `OpenFHECreditSession` for encryption/decryption
and calls calculation methods through its secretless evaluator view.
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
variance                   correlation_components
weighted_sum               risk_score
```

One local CKKS context and key set are created by the client role. The two
parent columns are encrypted once. The evaluator receives only compatible
components and ciphertexts. `PAYMENT_DIFF` is produced as ciphertext
subtraction and is the encrypted input for the aggregate methods.

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
source .venv-openfhe/bin/activate

python3 code/openfhe_direct/benchmarks/api_latency.py \
  --prepared-group data/prepared/examples/payment_diff_demo_group.csv \
  --repetitions 5 \
  --output-dir benchmark_runs/openfhe_direct_api \
  --overwrite
```

The previous command path remains as a compatibility entry point:

```bash
python3 code/heir/scripts/run_python_arithmetic_backend_benchmark.py \
  --prepared-group data/prepared/examples/payment_diff_demo_group.csv \
  --repetitions 5 \
  --output-dir benchmark_runs/openfhe_direct_api \
  --overwrite
```

Artifacts:

```text
results.csv
summary.json
REPORT.md
```
