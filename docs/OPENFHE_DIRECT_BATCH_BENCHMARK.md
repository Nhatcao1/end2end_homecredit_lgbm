# Direct OpenFHE-Python batch benchmark

This runner tests prepared installment rows without group-by, PSI, joins, or
an end-to-end feature pipeline.

For the five-operation primitive matrix at several row counts, the canonical
runner is:

```text
code/openfhe_direct/benchmarks/primitives.py
```

The remote client performs encryption and final audit decryption. The
secretless evaluator view maps the reported calculations to `session.py`:

| Report | Public session call |
|---|---|
| `CT+CT` | `evaluator.add(left_ct, right_ct)` |
| `CT-CT` | `evaluator.subtract(left_ct, right_ct)` |
| `CT×CT` | `evaluator.multiply(left_ct, right_ct)` |
| `CT+PT` | `evaluator.add_public_vector(left_ct, right_values)` |
| `CT×PT` | `evaluator.multiply_public_vector(left_ct, right_values)` |

One command can run 1k, 5k, 100k, and 1m real values sequentially:

```bash
python3 code/openfhe_direct/benchmarks/primitives.py \
  --prepared-dir data/prepared/installments_columns \
  --value-count 1000 5000 100000 1000000 \
  --repetitions 5 \
  --output-dir benchmark_runs/openfhe_direct_primitives \
  --overwrite
```

Each row-count run creates one OpenFHE context and key set at the client. All
five operations reuse its secretless evaluator view but receive fresh parent
encryption, so their outputs are not chained. The report includes Python
latency, client encryption, evaluator-method
evaluation, audit decryption, throughput, accuracy, and slowdown. It does not
run HEIR or compile generated C++.

All commands in this runbook use the reviewed profiles in
`code/openfhe_direct/profiles.py`. Ring dimension, slot capacity, depth,
modulus sizes, tolerances, and MIN/MAX comparison width are intentionally not
command-line options.

## Single-function runner

One command runs exactly one public `OpenFHECreditSession` function:

```text
code/openfhe_direct/benchmarks/single_function.py
```

Rows are packed into ciphertext chunks. For example:

| Values | Slots | Ciphertext chunks |
|---:|---:|---:|
| 1,000 | 8,192 | 1 |
| 5,000 | 8,192 | 1 |
| 10,000 | 8,192 | 2 |

`sum`, `mean`, and component operations return one result per ciphertext
chunk. This first batch test deliberately does not merge those results into a
global aggregate.

## Prepare parent columns once

```bash
python3 code/heir/scripts/prepare_full_installments_columns.py \
  --input-csv data/home_credit/installments_payments.csv \
  --output-dir data/prepared/installments_columns \
  --vector-size 8192 \
  --overwrite
```

This is client-side CSV sanitation and packing only. It does not run HEIR.

## Run separate functions

Start with 1,000 values and one repetition:

```bash
python3 code/openfhe_direct/benchmarks/single_function.py \
  --function subtract \
  --prepared-dir data/prepared/installments_columns \
  --value-count 1000 \
  --repetitions 1 \
  --output-dir benchmark_runs/openfhe_batch_subtract_1k \
  --overwrite
```

Then run SUM separately:

```bash
python3 code/openfhe_direct/benchmarks/single_function.py \
  --function sum \
  --prepared-dir data/prepared/installments_columns \
  --value-count 1000 \
  --repetitions 1 \
  --output-dir benchmark_runs/openfhe_batch_sum_1k \
  --overwrite
```

Then MEAN:

```bash
python3 code/openfhe_direct/benchmarks/single_function.py \
  --function mean \
  --prepared-dir data/prepared/installments_columns \
  --value-count 1000 \
  --repetitions 1 \
  --output-dir benchmark_runs/openfhe_batch_mean_1k \
  --overwrite
```

MINIMUM and MAXIMUM use the much heavier CKKS↔FHEW switching route. Start
with one 16-value ciphertext and one repetition. These two functions operate
on encrypted `AMT_INSTALMENT` and automatically select a safe public scale:

```bash
python3 -m code.openfhe_direct.benchmarks.single_function \
  --function minimum \
  --prepared-dir data/prepared/installments_columns \
  --value-count 16 \
  --repetitions 1 \
  --output-dir benchmark_runs/openfhe_minimum_16 \
  --overwrite
```

```bash
python3 -m code.openfhe_direct.benchmarks.single_function \
  --function maximum \
  --prepared-dir data/prepared/installments_columns \
  --value-count 16 \
  --repetitions 1 \
  --output-dir benchmark_runs/openfhe_maximum_16 \
  --overwrite
```

Change only `--function`, `--value-count`, and `--output-dir` for another
isolated run. Supported function names are shown by:

```bash
python3 code/openfhe_direct/benchmarks/single_function.py --help
```

Each output directory contains:

```text
REPORT.md
results.csv
summary.json
```

## Raw installments global SUM and MEAN

The single-function batch runner above reduces each ciphertext chunk
independently. Use the following dedicated runner when the required result is
one global PAYMENT_DIFF SUM or MEAN across 10k/50k raw installment rows:

```text
code/openfhe_direct/benchmarks/payment_diff_sum_mean.py
```

Activate and verify the OpenFHE-only environment first:

```bash
source .venv-openfhe/bin/activate
python3 -c "import openfhe; print(openfhe.__file__)"
```

Run global SUM:

```bash
python3 code/openfhe_direct/benchmarks/payment_diff_sum_mean.py \
  --operation sum \
  --installments data/home_credit/installments_payments.csv \
  --value-count 10000 50000 \
  --repetitions 5 \
  --output-dir benchmark_runs/openfhe_payment_diff_sum \
  --overwrite
```

Run global MEAN separately:

```bash
python3 code/openfhe_direct/benchmarks/payment_diff_sum_mean.py \
  --operation mean \
  --installments data/home_credit/installments_payments.csv \
  --value-count 10000 50000 \
  --repetitions 5 \
  --output-dir benchmark_runs/openfhe_payment_diff_mean \
  --overwrite
```

Each row-count run rereads the source CSV, filters only invalid numeric parent
pairs, creates a new direct OpenFHE-Python context, and encrypts both parent
columns on the client role. The secretless evaluator calculates PAYMENT_DIFF
and merges all encrypted chunk results. Only the returned final ciphertext is
decrypted by the client.
