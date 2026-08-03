# Direct OpenFHE-Python multi-group PAYMENT_DIFF benchmark

This is the small end-to-end path for several real groups selected from one
population-wide client preparation:

```text
all prepared installments groups
  → deterministic selection of N opaque groups
  → reassemble every selected group across preparation blocks
  → one shared OpenFHE-Python CKKS context
  → for each selected group separately:
      encrypt AMT_INSTALMENT and AMT_PAYMENT
      encrypted PAYMENT_DIFF
      encrypted SUM, MEAN, sample VARIANCE, MINIMUM, MAXIMUM
  → final accuracy-audit decryption
```

There is no intermediate decryption. Client grouping and padding have already
occurred in the population preparation and are outside the HE latency. A group
larger than the preparation bucket is not dropped: its blocks are reassembled
into one complete group before encryption.

The benchmark constructs `OpenFHECreditSession(enable_minmax=True)`.
Its public `minimum()` and `maximum()` methods use OpenFHE-Python CKKS↔FHEW
switching. All groups reuse that one live context. The benchmark does not
import OpenFHE, configure keys, run the HEIR compiler, or generate C++.

This remains a same-process exception to the normal client/evaluator split.
The current Python route does not expose transport of every live CKKS↔FHEW
switching component. `summary.json` therefore records
`client_evaluator_separated: false`; ordinary CKKS/BGV benchmarks record
`true`.

## Prepare the complete real group population

Run this once. It scans the full installments table and writes opaque group
metadata plus sharded parent rows.

```bash
python3 code/heir/scripts/prepare_installments_group_blocks.py \
  --input-csv data/home_credit/installments_payments.csv \
  --output-dir data/prepared/installments_group_population \
  --bucket-size 128 \
  --vector-size 8192 \
  --overwrite
```

## Select five real groups and run each separately

```bash
python3 code/openfhe_direct/benchmarks/payment_diff_multigroup.py \
  --prepared-population-dir data/prepared/installments_group_population \
  --group-count 5 \
  --selection spread \
  --repetitions 1 \
  --ring-dimension 16384 \
  --output-dir benchmark_runs/openfhe_payment_diff_e2e_real_5groups \
  --overwrite
```

`spread` selects across the complete group-size distribution, so the run set
is derived from all available groups rather than from committed demo files.
Use `--selection largest` for a stress-oriented set, `--selection first` for
the first opaque IDs, or `--opaque-group-id 12 98 301` for explicit private
selection. `--group-count 0` means every eligible group and is not recommended
for a first MIN/MAX run.

The runner scans the prepared parent shards once, retains only the selected
groups, and removes invalid numeric parent rows at the client boundary. It
chooses one shared slot width that covers the largest complete selected group.
It also chooses a public power-of-two scale covering both parents and the
derived `PAYMENT_DIFF` comparison range. Use `--slot-count` or `--input-scale`
only when an explicit public contract is required.

The old `--prepared-group ...` path remains available only for small committed
smoke fixtures.

Artifacts:

```text
REPORT.md
accuracy.csv
timings.csv
summary.json
```
