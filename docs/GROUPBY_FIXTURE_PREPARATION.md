# Real-installments groupby fixture

`prepare_payment_diff_groupby_test_data.py` is the client-only first step for
testing the original installments aggregation. It selects a small set of
complete `SK_ID_CURR` groups from `installments_payments.csv`, rather than
pretending that a raw contiguous CSV slice is a complete groupby workload.

It makes two source passes. The first finds groups with finite numeric
`AMT_PAYMENT` and `AMT_INSTALMENT` values that fit one chosen block. The second
collects all rows for those selected applicants. Thus a 10-group fixture is a
real, complete groupby sample while using only a small HE input.

The resulting files have distinct privacy roles:

| File | Role | May go to HE evaluator? |
|---|---|---|
| `he_ready/group_blocks.csv` | Padded numeric parent columns and validity masks | Yes |
| `client_private/group_mapping.csv` | `SK_ID_CURR` to opaque-group mapping | No |
| `client_private/pandas_groupby_reference.csv` | Plaintext audit reference | No |
| `layout_manifest.json` | Counts, capacities and padding metadata | Yes, if that leakage is accepted |

Each selected applicant occupies one `bucket_size` segment. For a real source
row, `validity_mask=1`; padding writes `AMT_PAYMENT=0`,
`AMT_INSTALMENT=0`, and `validity_mask=0`. No `PAYMENT_DIFF` value is written
to the HE-ready input. The later HE benchmark must compute:

```python
PAYMENT_DIFF = AMT_INSTALMENT - AMT_PAYMENT
```

after encryption. The private reference is equivalent to the following after
the documented row filter:

```python
filtered["PAYMENT_DIFF"] = filtered["AMT_INSTALMENT"] - filtered["AMT_PAYMENT"]
filtered.groupby("SK_ID_CURR")["PAYMENT_DIFF"].agg(["count", "sum", "mean", "var"])
```

The initial fixture deliberately selects only groups that fit one block. A
later scalable layout can split an oversized applicant across blocks, but then
its encrypted partial aggregates must be merged before comparison with the
one-row Pandas groupby result.

## First encrypted step

`run_grouped_payment_diff_sum_benchmark.py` is the first HE consumer of this
fixture. It creates one shared CKKS context, then processes each prepared
block independently:

```text
encrypt AMT_INSTALMENT + AMT_PAYMENT + validity mask
  -> HEIR encrypted_subtract(PAYMENT_DIFF)
  -> HEIR encrypted_sum(PAYMENT_DIFF)  = one encrypted group sum
  -> HEIR encrypted_sum(validity mask) = one encrypted group count
  -> decrypt only for the audit table
```

This proves the group layout, the after-encryption feature calculation, and
one aggregate per group. It is intentionally not yet an efficient many-group
packing implementation: every 128-lane applicant block occupies one 8192-lane
CKKS ciphertext. The report makes this explicit.
