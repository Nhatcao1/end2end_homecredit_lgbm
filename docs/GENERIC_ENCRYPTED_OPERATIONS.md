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
