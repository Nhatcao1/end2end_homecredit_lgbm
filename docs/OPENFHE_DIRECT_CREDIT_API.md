# Direct OpenFHE-Python credit API

This is the small application path:

```text
prepared credit CSV → local OpenFHE Python → encrypt → calculate → decrypt
```

It has no gateway URL, HTTP client, HEIR compiler, MLIR, or CMake runner.

## Files

| File | Responsibility |
|---|---|
| `code/openfhe_direct/session.py` | CKKS context and calculation methods |
| `code/openfhe_direct/prepared_data.py` | Prepared credit CSV loading |
| `code/openfhe_direct/credit_rating_example.py` | Readable application flow |

## Method mapping

| Public method | OpenFHE operation |
|---|---|
| `encrypt` | `MakeCKKSPackedPlaintext` + `Encrypt` |
| `decrypt` | `Decrypt` |
| `add` | `EvalAdd` |
| `subtract` | `EvalSub` |
| `multiply` | `EvalMult` |
| `add_public_scalar` | scalar `EvalAdd` |
| `add_public_vector` | plaintext-vector `EvalAdd` |
| `multiply_public_scalar` | scalar `EvalMult` |
| `multiply_public_vector` | plaintext-vector `EvalMult` |
| `square` | `EvalMult(x, x)` |
| `sum` | `EvalSum` |
| `mean` | `EvalSum` then scalar `EvalMult(1/n)` |
| `variance_components` | encrypted Σx and Σx² |
| `covariance_components` | encrypted Σx, Σy, and Σxy |
| `correlation_components` | encrypted sums, squared sums, and product sum |
| `weighted_sum` | plaintext-vector `EvalMult` then `EvalSum` |
| `risk_score` | weighted sum then scalar `EvalAdd(bias)` |

`EvalMultKeyGen` and `EvalSumKeyGen` run once when the session is created.

The Python interpreter must contain the official `openfhe` binding built for
the same OpenFHE version as the installed C++ runtime. The class checks that
every required operation exists before generating keys.

## Run

```bash
source .venv-heir-python/bin/activate

python3 code/openfhe_direct/credit_rating_example.py \
  --prepared-group data/prepared/examples/payment_diff_demo_group.csv \
  --multiplicative-depth 4 \
  --output benchmark_runs/openfhe_direct_credit_result.json
```

The example calculates `PAYMENT_DIFF = AMT_INSTALMENT - AMT_PAYMENT` after
both parent columns are encrypted.

## Latency benchmark

The matching benchmark calls these same public methods directly:

```bash
python3 code/openfhe_direct/benchmark.py \
  --prepared-group data/prepared/examples/payment_diff_demo_group.csv \
  --repetitions 5 \
  --multiplicative-depth 4 \
  --output-dir benchmark_runs/openfhe_direct_api \
  --overwrite
```

It reports one median call latency per method plus final audit accuracy.

For larger prepared row batches and one-function-per-command execution, see
[`OPENFHE_DIRECT_BATCH_BENCHMARK.md`](OPENFHE_DIRECT_BATCH_BENCHMARK.md).
