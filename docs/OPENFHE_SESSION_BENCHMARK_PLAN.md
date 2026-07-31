# OpenFHE session and benchmark development plan

## Permanent rule

All homomorphic-encryption functionality must be exposed as a public method in:

```text
code/openfhe_direct/session.py
```

Benchmark and example files must call those session methods. They must not
implement cryptographic operations themselves.

Every session invocation in benchmark code must have an adjacent comment that
names the public function being called:

```python
# HE API call: OpenFHECreditSession.mean(payment_diff_ct)
mean_ct = session.mean(payment_diff_ct)
```

Use the exact prefix `# HE API call:` so this boundary is easy to review and
can be checked by tests.

The intended usage is:

```python
session = OpenFHECreditSession(...)

left_ct = session.encrypt(left)
right_ct = session.encrypt(right)

difference_ct = session.subtract(left_ct, right_ct)
mean_ct = session.mean(difference_ct)
maximum_ct = session.maximum(difference_ct)

mean = session.decrypt(mean_ct)
maximum = session.decrypt(maximum_ct)
```

## Layer ownership

| Layer | Responsibility |
|---|---|
| `session.py` | Context creation, keys, encryption, ciphertext operations, scheme switching, decryption |
| Prepared-data modules | Numeric sanitation, masks, padding, and public group layout |
| Benchmark files | Load prepared inputs, call session methods, record latency, audit accuracy, write reports |
| Example files | Show a readable application flow using the public session API |

HEIR-generated MLIR/C++ runners under `code/heir/scripts/` are retained as
historical experiments. They are not the implementation path for new direct
OpenFHE-Python benchmarks.

## What benchmarks may do

Benchmark files may:

- load prepared numeric columns;
- create one session through the public constructor;
- call `session.encrypt()` and other public session methods;
- measure setup, encryption, function, and audit latency;
- calculate a plaintext Python reference;
- decrypt only at the final accuracy boundary;
- write CSV, JSON, and Markdown reports.

Benchmark files must not:

- import `openfhe` directly;
- call `EvalAdd`, `EvalSub`, `EvalMult`, `EvalSum`, scheme-switching methods,
  or key-generation methods;
- configure CKKS/FHEW contexts directly;
- generate MLIR, C++, or CMake projects;
- contain a second implementation of SUM, MEAN, VARIANCE, MIN, or MAX.

## Public session target

The canonical session API should expose:

```text
encrypt
decrypt

add
subtract
multiply

add_public_scalar
add_public_vector
multiply_public_scalar
multiply_public_vector

square
sum
mean
variance
minimum
maximum

variance_components
covariance_components
correlation_components
weighted_sum
risk_score
```

## Current consolidation status

| Capability | Current state | Required action |
|---|---|---|
| Encrypt/decrypt | In `code/openfhe_direct/session.py` | Keep |
| Add/subtract/multiply | In `code/openfhe_direct/session.py` | Keep |
| Public scalar/vector arithmetic | In `code/openfhe_direct/session.py` | Keep |
| Square/sum/mean/variance | In `code/openfhe_direct/session.py` | Keep |
| Variance/covariance/correlation components | In `code/openfhe_direct/session.py` | Keep |
| Weighted sum/risk score | In `code/openfhe_direct/session.py` | Keep |
| CKKS↔FHEW minimum/maximum | Public methods in `code/openfhe_direct/session.py` | Complete |
| Real-data primitive matrix | `code/openfhe_direct/primitive_benchmark.py`; calls only session methods | Complete |
| Raw-installments global SUM/MEAN | `code/openfhe_direct/payment_diff_sum_mean_benchmark.py`; derives encrypted PAYMENT_DIFF and merges every ciphertext chunk | Complete |
| Population-backed multi-group benchmark | Selects from all prepared opaque groups, reassembles complete groups, then calls only `OpenFHECreditSession` methods | Complete |

The low-level scheme-switching helper remains private to the session layer.
Benchmarks do not import it or copy its configuration.

## Procedure for adding a new HE function

1. Define the public method and ciphertext input/output contract in
   `session.py`.
2. Implement the OpenFHE operation inside the session layer.
3. Add a focused unit test for the session method.
4. Add the method to the readable example when useful.
5. Make benchmarks call the method; do not copy its implementation.
6. Add an adjacent `# HE API call:` comment at every benchmark call site.
7. Record setup requirements, multiplicative depth, scale/range contract,
   padding behavior, and whether scheme switching is used.
8. Run a small server smoke test before increasing groups or row counts.

## Benchmark rollout

1. Primitive matrix over real parent columns:
   `code/openfhe_direct/primitive_benchmark.py` calls `add`, `subtract`,
   `multiply`, `add_public_vector`, and `multiply_public_vector`. Each
   operation runs independently through `OpenFHECreditSession`; one shared
   context is reused within each row-count run.
2. Independent reductions:
   `code/openfhe_direct/payment_diff_sum_mean_benchmark.py` reads the raw
   installments CSV separately for SUM and MEAN. It derives PAYMENT_DIFF
   after parent encryption and returns one global encrypted result across all
   runtime chunks. Variance remains a separate benchmark.
3. Independent scheme-switching runs:
   `minimum` and `maximum`.
4. Prepare the full group population, select two real opaque groups, and
   execute each group separately in one shared session.
5. Increase to five groups only after the two-group accuracy report passes.
6. Larger row batches and global chunk merging are separate future work.

## Acceptance criteria

A new benchmark is acceptable only when:

- its HE calculation is a call to a public session method;
- every session call has an adjacent `# HE API call:` comment;
- it contains no direct OpenFHE or HEIR operation;
- ciphertext remains encrypted between operations;
- setup, function latency, and audit decryption are clearly separated;
- Python reference values and HE audit errors are reported;
- unsupported functionality is marked explicitly rather than approximated
  silently.

The primitive benchmark additionally must:

- accept several runtime row counts without regenerating a kernel;
- read sanitized real installment parents;
- report Python, encryption, session-method evaluation, and audit-decryption
  latency separately;
- report evaluation-only and online slowdown versus Python;
- contain no `heir-opt`, generated source, CMake, or direct `Eval*` call.

The raw-installments SUM/MEAN benchmark additionally must:

- take `installments_payments.csv` directly rather than a prepared-column
  directory;
- run SUM and MEAN as separate benchmark invocations;
- remove missing/non-finite parent pairs only at the client input boundary;
- merge all encrypted chunk results into one global result;
- compare that result with the equivalent Python PAYMENT_DIFF SUM or MEAN.
