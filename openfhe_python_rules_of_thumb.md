# OpenFHE-Python rules for the exposed function layer

This is the version-1 contract. A non-HE user declares calculations before
encryption. The planner chooses the CKKS depth and required evaluation keys.
Parent encryption and final decryption belong to the remote data owner.
Public evaluator functions accept ciphertext operands only.

```text
Remote data owner
  plan → context/key setup → encrypt parent columns
  sends: context + evaluation keys + ciphertexts
                         ↓
Untrusted evaluator
  ciphertext-only calculation; no secret key
  returns: result ciphertexts
                         ↓
Remote data owner
  final decryption
```

## Front-facing functions

```python
he.add(left_ct, right_ct)
he.subtract(left_ct, right_ct)
he.multiply(left_ct, right_ct)
he.square(column_ct)
he.sum(column_ct)
he.mean(column_ct)
he.variance(column_ct)
```

`mean()` and `variance()` obtain the public element count from the encrypted
vector metadata. Users do not pass depth, modulus sizes, rotation indices,
relinearization decisions, rescale calls, or bootstrap positions.

## Backend rules

1. Use CKKS for the current approximate real-number analytics.
2. Build and inspect the complete immutable DAG before encryption.
3. Compute depth from the longest sequential multiplication path.
4. Treat addition, subtraction, and rotation as depth cost zero.
5. Conservatively charge one level for CT×CT, square, and CT×PT.
6. Use ordinary `EvalMult`/`EvalSquare`; hide relinearization.
7. Use one tested automatic CKKS scaling mode; do not put manual `Rescale()`
   calls inside business functions.
8. Generate multiplication and sum/rotation keys only when the plan needs
   them.
9. Keep ciphertext values immutable and reuse shared DAG nodes.
10. Store element count, slot count, layout, context ID, and key ID as public
    encrypted-object metadata.
11. Reject incompatible contexts and unsupported/over-depth workflows before
    evaluation.
12. Decrypt only at the explicit application audit/output boundary.
13. Never send the secret key to the evaluator. A production transport sends
    only the compatible context, required evaluation keys, public ciphertext
    metadata, and ciphertext artifacts.

## Conservative operation manifest

These costs are wrapper policies and must be checked by benchmarks.

| Function | Depth cost | Multiplication key | Sum/rotation key |
|---|---:|---|---|
| `add`, `subtract` | 0 | No | No |
| `multiply`, `square` | 1 | Yes | No |
| `sum` | 0 | No | Yes |
| `mean` | 1 | No | Yes |
| `variance` | 2 | Yes | Yes |

The physical plan is reviewable:

```json
{
  "scheme": "CKKS",
  "operations": ["mean", "subtract", "sum", "variance"],
  "required_depth": 2,
  "context_depth": 2,
  "needs_eval_mult_key": true,
  "needs_eval_sum_key": true,
  "automatic_scaling": true,
  "bootstrapping": false
}
```

## Intentionally outside version 1

- BGV/BFV exact-integer planning
- arbitrary bootstrapping placement
- manual rescaling or delayed relinearization
- custom/hoisted rotation optimisation
- polynomial, covariance, and correlation planning
- MIN/MAX and CKKS↔FHEW planning
- automatic range and precision tuning

These remain separate experiments until they have tested profiles. They must
not silently enter a normal CKKS workflow.

## Code map

| File | Responsibility |
|---|---|
| `code/openfhe_direct/api.py` | Non-HE workflow and ciphertext-only functions |
| `code/openfhe_direct/planner.py` | Operation rules, depth analysis, key manifest |
| `code/openfhe_direct/session.py` | The only normal OpenFHE-Python execution layer |
| `code/openfhe_direct/credit_rating_example.py` | Minimal PAYMENT_DIFF example |

The example models the client/evaluator handoff with separate Python objects
inside one process so it remains easy to review. In deployment, serialize that
same evaluator context, evaluation keys, and ciphertext bundle across the
process or machine boundary. The evaluator view deliberately contains no
public encryption key and no secret decryption key.

## Primary references

- [OpenFHE-Python](https://github.com/openfheorg/openfhe-python)
- [CKKS arithmetic example](https://github.com/openfheorg/openfhe-development/blob/main/src/pke/examples/simple-real-numbers.cpp)
- [OpenFHE CryptoContext API](https://openfhe-development.readthedocs.io/en/latest/api/classlbcrypto_1_1CryptoContextImpl.html)
