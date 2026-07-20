# Generic encrypted-column operations

## Rule

The client may turn null/non-finite values into a numeric value plus a validity
mask, encode categories, and pack fixed-size vectors. It must not calculate a
source feature expression before encryption. Feature arithmetic runs over
encrypted columns.

## API boundary

```text
nullable input columns
  -> client value + validity-mask packing
  -> encrypt
  -> encrypted_binary(add | subtract | multiply)
  -> encrypted reductions (count | sum | sum_squares)
  -> decrypt only for the client accuracy audit
```

`prepare_nullable_column()` is deliberately limited to input representation. It
does not derive ratios, differences, means, or other business features.

## Capability matrix

| Python expression | Route | Status |
|---|---|---|
| `a + b`, `a - b`, `a * b` | native CKKS | Implemented generic API |
| `count`, `sum`, `sum(x * x)` | native CKKS | Existing K01/K02 contracts |
| `a / b` | reciprocal polynomial + multiply | Planned |
| `sum / count`, variance | reciprocal polynomial | Planned |
| `x > threshold` | CKKS sign polynomial | Planned |
| `min` / `max` | OpenFHE CKKS-to-FHEW switching | Planned, separate from HEIR-generated CKKS |

## Representative source benchmarks

We deliberately test a compact set of source expressions rather than treating
every original feature function as a separate HE program.

| Benchmark | Original source expression | Current status | Why it is included |
|---|---|---|---|
| `installments_payment_diff` | `AMT_INSTALMENT - AMT_PAYMENT` | Executable exact CKKS | Validates raw two-column arithmetic, null packing, HEIR lowering, timing, and decryption accuracy. |
| `application_days_employed_perc` | `DAYS_EMPLOYED / DAYS_BIRTH` | Deferred | Validates the required reciprocal/division design and source sentinel-to-null handling. |
| `installments_dpd_clip` | `max(DAYS_ENTRY_PAYMENT - DAYS_INSTALMENT, 0)` | Deferred | Covers comparison/clipping, which must remain visibly separate from ordinary CKKS arithmetic. |

After the arithmetic benchmark, encrypted mask combination and masked grouped
reductions use the same generic `multiply` / `count_sum_squares` contracts.
`min`, `max`, and `nunique` are not silently included in this first lane.

Generate and run the exact first benchmark on the HEIR/OpenFHE server:

```bash
python3 code/heir/scripts/generate_generic_binary_ckks.py \
  --operation subtract \
  --vector-size 8192 \
  --output-dir benchmark_runs/generated_ckks/generic_subtract_8192

python3 code/heir/scripts/run_representative_benchmarks.py \
  --benchmark installments_payment_diff \
  --backend heir-generated-ckks \
  --data-dir data/home_credit \
  --row-limit 8192 \
  --vector-size 8192 \
  --generated-dir benchmark_runs/generated_ckks/generic_subtract_8192 \
  --openfhe-dir /usr/local/lib/OpenFHE \
  --run-name payment_diff_8192_01
```

## Tiny review example: `PAYMENT_PERC`, `DPD`, and `DBD`

For an executable three-row review example, use one command. It generates and
keeps the MLIR internally, then lowers, translates, compiles, encrypts,
evaluates, decrypts for audit, and writes one result table:

```bash
python3 code/heir/scripts/run_quick_installments_heir_demo.py \
  --output-dir benchmark_runs/quick_installments_heir_01 \
  --overwrite \
  --vector-size 8 \
  --openfhe-dir /usr/local/lib/OpenFHE
```

Read `result_table.csv` or `quick_demo_report.md`; do not manually run MLIR
files. The generated MLIR remains under `payment_perc/` and
`positive_difference/` for review. `payment_perc_newton.mlir` shows a two-step
reciprocal approximation entirely after encryption. The public scale is a
representation/range policy; it does not calculate a feature client-side.
`positive_difference_smoothstep.mlir` is one generic ordered operation: use
entry-payment minus installment for DPD, and reverse inputs for DBD. It is
approximate near zero. Exact clipping is still the separate OpenFHE CKKS-to-
FHEW comparison experiment.

## First ciphertext proof: `PAYMENT_PERC` and `PAYMENT_DIFF`

### Required precision probe: `PAYMENT_PERC` before aggregation

`PAYMENT_PERC` is a reciprocal approximation, so it must be validated alone
before it is passed to any aggregation kernel. The dedicated probe requests a
CKKS multiplicative-depth budget explicitly and records the parameter calls
emitted by HEIR/OpenFHE in `result.json`. The server's installed HEIR release
does not expose `mul-depth` through its `scheme-to-openfhe` pipeline, so this
probe patches only the translated OpenFHE `SetMultiplicativeDepth(...)` call
before compilation and records both the inferred and requested values. It
deliberately does not run sum.

```bash
python3 code/heir/scripts/run_payment_perc_depth_probe.py \
  --output-dir benchmark_runs/payment_perc_depth12_01 \
  --overwrite \
  --vector-size 8 \
  --ckks-mul-depth 12 \
  --openfhe-dir /usr/local/lib/OpenFHE
```

The client only prepares a safe denominator and encrypted validity flag:
missing, non-positive, or out-of-range installment values are replaced with the
public scale before encryption, and the encrypted result is masked afterward.
It never computes the ratio client-side. The current public contract is
`AMT_INSTALMENT / 1000` in `[0.5, 1.0]`; change and validate that contract for
real data before using this kernel.

Only after `comparison.csv` shows acceptable relative error should the result
ciphertext be passed into encrypted sum. Do not introduce bootstrapping before
this isolated depth-12 probe has failed and its generated parameters have been
reviewed.

Use this focused command to encrypt only `AMT_PAYMENT` and
`AMT_INSTALMENT`, calculate both requested features, and pass each encrypted
feature vector to a separate HEIR-generated `sum` kernel. Decryption happens
only after both encrypted calculations for the audit comparison:

```bash
python3 code/heir/scripts/run_payment_features_ciphertext_demo.py \
  --output-dir benchmark_runs/payment_features_ciphertext \
  --overwrite \
  --vector-size 8 \
  --openfhe-dir /usr/local/lib/OpenFHE
```

Review `comparison.csv` for row-level feature accuracy and
`sum_comparison.csv` for the two sums. Each feature directory keeps both
`ciphertexts/result.ct` (the encrypted feature vector) and
`ciphertexts/sum.ct` (the encrypted scalar sum), along with encrypted inputs.
The generated sum MLIR remains in `sum_kernel/source.mlir`.
The sum uses a balanced ciphertext-only addition tree. It deliberately avoids a
plaintext-zero loop accumulator, so it can preserve the CKKS level of a feature
ciphertext produced by the preceding kernel.

This step implements only ungrouped sum. It does not attempt groupby, count,
mean, variance, categorical means, or max. Padding contributes zero to the sum;
the validity mask remains part of `PAYMENT_PERC` so padded rows cannot affect
that feature.

## Timing and accuracy

Every benchmark records four independent durations:

| Timing | Purpose |
|---|---|
| Python calculation | Headline plaintext baseline; only the source expression |
| Encryption | Recorded operational cost; excluded from headline comparison |
| Encrypted evaluation | Headline HE calculation time |
| Decryption | Accuracy-audit cost; excluded from headline comparison |

The decrypted result is compared to the original Python expression using maximum
absolute and relative error. No benchmark may claim that client-side feature
calculation is encrypted execution.
