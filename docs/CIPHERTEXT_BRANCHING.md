# Ciphertext branching: what “copy” means

## Short version

We do **not** decrypt and re-encrypt `PAYMENT_DIFF` to use it in multiple
calculations. The feature is calculated once after encryption, saved as an HE
ciphertext artifact, then reloaded into independent encrypted branches.

```text
encrypted AMT_INSTALMENT + encrypted AMT_PAYMENT
                     ↓
             encrypted PAYMENT_DIFF
                     ↓ serialize
          payment_diff_<batch>.ct
              ↙                 ↘ deserialize
         sum branch         square-sum branch
              ↓                 ↓
          encrypted sum   encrypted sum-of-squares
```

## Why materialize branches?

The generated HEIR/OpenFHE reduction function may reuse or mutate its input
ciphertext buffer. If `sum` and `sum-of-squares` receive the same in-memory
ciphertext object, one branch can change the other branch's input.

Therefore the full workload does this:

1. Calculate `PAYMENT_DIFF` once in HE.
2. Serialize the resulting ciphertext bundle to
   `ciphertexts/payment_diff_batches/payment_diff_<batch>.ct`.
3. Deserialize it twice: one independent bundle for `sum`, one for
   `sum-of-squares`.
4. Keep the original saved feature artifact for later pipeline stages.

There is no plaintext feature result at steps 1–4.

## What is copied?

| Item | Copied? | Reason |
|---|---|---|
| CKKS context / keys | No | One context and key set serve the whole workload. |
| Parent plaintext values | No after encryption | Parent values are encrypted once per batch. |
| `PAYMENT_DIFF` ciphertext bundle | Materialized and reloaded | Gives each mutable downstream branch independent ownership. |
| Decrypted feature values | No | Only final audit scalars are decrypted by the key owner. |

## Cost in the report

`Ciphertext materialization + branch accumulation` includes:

- writing the feature ciphertext bundle;
- reloading the `sum` and `sum-of-squares` branch bundles;
- adding encrypted per-batch moments into global encrypted totals.

It is part of the HE workload headline because real encrypted pipelines must
pay this ownership/persistence cost. It is not hidden as client preprocessing.

## After the benchmark

The saved `PAYMENT_DIFF` ciphertext batches can feed a later HE stage without
recalculating the feature from parent columns. A later evaluator must use the
same compatible CKKS context and evaluation keys; it cannot mix ciphertexts
from an unrelated HE session.
