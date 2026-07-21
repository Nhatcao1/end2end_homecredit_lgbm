# Full encrypted workload numerical notes

## Deferred: public amount scaling before CKKS encryption

The first full-data `PAYMENT_DIFF` attempt used raw monetary values and failed
at final audit decryption with excessive CKKS approximation error. Do not label
that run as a successful benchmark.

For the next implementation, encode both parent amount columns with a public
representation scale before encryption:

```text
encoded_payment = AMT_PAYMENT / S
encoded_installment = AMT_INSTALMENT / S
```

The HE kernel must still calculate the feature after encryption:

```text
encoded_PAYMENT_DIFF = encoded_installment - encoded_payment
```

This is numeric representation, not client-side feature engineering. Restore
audit outputs only after decryption:

| Output | Restore rule |
|---|---|
| sum | `decoded_sum × S` |
| mean | `decoded_mean × S` |
| variance | `decoded_variance × S²` |

The scale `S` must be a reviewed public range contract. The report should
record it. No automatic scale inferred from secret data should be introduced.

## Also required

The existing fixed-count batch reduction is a long sequential reduction for
8192 values. Replace it with a balanced packed reduction before retrying the
full raw-money workload. Cross-batch accumulation is already a bounded-memory
binary tree; the per-batch reduction needs the same treatment.
