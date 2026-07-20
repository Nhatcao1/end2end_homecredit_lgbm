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

For an inspectable three-row example—not a benchmark—emit both expected
notebook-style plaintext outputs and the proposed HEIR MLIR:

```bash
python3 code/heir/examples/quick_installments_features.py \
  --output-dir benchmark_runs/quick_installments_features \
  --vector-size 8
```

Read `expected_plaintext.json` first. `payment_perc_newton.mlir` shows a
two-step reciprocal approximation entirely after encryption. The public scale
is a representation/range policy; it does not calculate a feature client-side.
`positive_difference_smoothstep.mlir` is one generic ordered operation:
use entry-payment minus installment for DPD, and reverse the inputs for DBD.
It is approximate near zero. Exact clipping is still the separate OpenFHE
CKKS-to-FHEW comparison experiment.

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
