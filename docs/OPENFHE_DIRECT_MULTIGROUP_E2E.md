# Direct OpenFHE-Python multi-group PAYMENT_DIFF benchmark

This is the small end-to-end path for several already prepared groups:

```text
prepared parent columns per group
  → one shared OpenFHE-Python CKKS context
  → encrypt AMT_INSTALMENT and AMT_PAYMENT
  → encrypted PAYMENT_DIFF
  → encrypted SUM, MEAN, sample VARIANCE, MINIMUM, MAXIMUM
  → final accuracy-audit decryption
```

There is no intermediate decryption. Client grouping and padding have already
occurred in the prepared input files and are outside the HE latency.

The benchmark uses the existing `CkksSession`, including its OpenFHE-Python
CKKS↔FHEW MIN/MAX operations. All groups reuse that one live context.
The direct package re-exports this session for the benchmark; it does not run
the HEIR compiler or generated C++.

## Five-group smoke command

```bash
python3 code/openfhe_direct/multigroup_e2e_benchmark.py \
  --prepared-group \
    data/prepared/examples/payment_diff_demo_group.csv \
    data/prepared/examples/payment_diff_demo_group_002.csv \
    data/prepared/examples/payment_diff_demo_group_003.csv \
    data/prepared/examples/payment_diff_demo_group_004.csv \
    data/prepared/examples/payment_diff_demo_group_005.csv \
  --repetitions 1 \
  --ring-dimension 16384 \
  --output-dir benchmark_runs/openfhe_payment_diff_e2e_5groups \
  --overwrite
```

The runner chooses one shared slot width that covers the largest prepared
group. It chooses a public power-of-two scale covering both parents and the
derived `PAYMENT_DIFF` comparison range. Use `--slot-count` or
`--input-scale` only when an explicit public contract is required.

Artifacts:

```text
REPORT.md
accuracy.csv
timings.csv
summary.json
```
