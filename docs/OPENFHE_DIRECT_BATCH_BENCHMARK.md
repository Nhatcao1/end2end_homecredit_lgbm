# Direct OpenFHE-Python batch benchmark

This runner tests prepared installment rows without group-by, PSI, joins, or
an end-to-end feature pipeline.

One command runs exactly one public `OpenFHECreditSession` function:

```text
code/openfhe_direct/batch_benchmark.py
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
python3 code/openfhe_direct/batch_benchmark.py \
  --function subtract \
  --prepared-dir data/prepared/installments_columns \
  --value-count 1000 \
  --slot-count 8192 \
  --repetitions 1 \
  --multiplicative-depth 4 \
  --output-dir benchmark_runs/openfhe_batch_subtract_1k \
  --overwrite
```

Then run SUM separately:

```bash
python3 code/openfhe_direct/batch_benchmark.py \
  --function sum \
  --prepared-dir data/prepared/installments_columns \
  --value-count 1000 \
  --slot-count 8192 \
  --repetitions 1 \
  --multiplicative-depth 4 \
  --output-dir benchmark_runs/openfhe_batch_sum_1k \
  --overwrite
```

Then MEAN:

```bash
python3 code/openfhe_direct/batch_benchmark.py \
  --function mean \
  --prepared-dir data/prepared/installments_columns \
  --value-count 1000 \
  --slot-count 8192 \
  --repetitions 1 \
  --multiplicative-depth 4 \
  --output-dir benchmark_runs/openfhe_batch_mean_1k \
  --overwrite
```

Change only `--function`, `--value-count`, and `--output-dir` for another
isolated run. Supported function names are shown by:

```bash
python3 code/openfhe_direct/batch_benchmark.py --help
```

Each output directory contains:

```text
REPORT.md
results.csv
summary.json
```
