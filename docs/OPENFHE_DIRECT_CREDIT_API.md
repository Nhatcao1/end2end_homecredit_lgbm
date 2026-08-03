# Direct OpenFHE-Python credit API

This is the small application path:

```text
remote data owner: prepared CSV → encrypt
                                 ↓
evaluator: context + evaluation keys + ciphertexts → calculate
                                 ↓
remote data owner: result ciphertexts → decrypt
```

It has no gateway URL, HTTP client, HEIR compiler, MLIR, or CMake runner.

## Files

| File | Responsibility |
|---|---|
| `code/openfhe_direct/api.py` | Non-HE workflow and ciphertext-only functions |
| `code/openfhe_direct/planner.py` | Depth and evaluation-key rules |
| `code/openfhe_direct/session.py` | CKKS context and OpenFHE execution methods |
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
| `variance` | encrypted sample variance from Σx and Σx² |
| `minimum` | CKKS↔FHEW encrypted minimum |
| `maximum` | CKKS↔FHEW encrypted maximum |
| `covariance_components` | encrypted Σx, Σy, and Σxy |
| `correlation_components` | encrypted sums, squared sums, and product sum |
| `weighted_sum` | plaintext-vector `EvalMult` then `EvalSum` |
| `risk_score` | weighted sum then scalar `EvalAdd(bias)` |

The planner runs before encryption. `EvalMultKeyGen` and `EvalSumKeyGen` run
once when the session is created, and only when the declared workflow needs
the corresponding keys.

The Python interpreter must contain the official `openfhe` binding built for
the same OpenFHE version as the installed C++ runtime. The class checks that
every required operation exists before generating keys.

Create the session with `enable_minmax=True` and a public `input_scale` to
enable the CKKS↔FHEW MIN/MAX context. Basic arithmetic benchmarks leave this
disabled to avoid paying the scheme-switching setup cost.

## Run

```bash
source .venv-heir-python/bin/activate

python3 code/openfhe_direct/credit_rating_example.py \
  --prepared-group data/prepared/examples/payment_diff_demo_group.csv \
  --output benchmark_runs/openfhe_direct_credit_result.json
```

The example calculates `PAYMENT_DIFF = AMT_INSTALMENT - AMT_PAYMENT` after
both parent columns are encrypted. It also returns encrypted SUM, MEAN, and
sample VARIANCE. The planner selects the CKKS depth and key requirements; the
application does not pass them. For reviewability this example hands objects
between client and evaluator roles in one process. Production transports the
serialized context, evaluation keys, ciphertext metadata, and ciphertexts;
the secret key remains with the remote data owner.

## Latency benchmark

The matching benchmark calls these same public methods directly:

```bash
python3 code/openfhe_direct/benchmarks/api_latency.py \
  --prepared-group data/prepared/examples/payment_diff_demo_group.csv \
  --repetitions 5 \
  --output-dir benchmark_runs/openfhe_direct_api \
  --overwrite
```

It reports one median call latency per method plus final audit accuracy.

For larger prepared row batches and one-function-per-command execution, see
[`OPENFHE_DIRECT_BATCH_BENCHMARK.md`](OPENFHE_DIRECT_BATCH_BENCHMARK.md).

For one shared-context PAYMENT_DIFF execution over several prepared groups,
see
[`OPENFHE_DIRECT_MULTIGROUP_E2E.md`](OPENFHE_DIRECT_MULTIGROUP_E2E.md).

The permanent development rule and MIN/MAX consolidation plan are in
[`OPENFHE_SESSION_BENCHMARK_PLAN.md`](OPENFHE_SESSION_BENCHMARK_PLAN.md).
