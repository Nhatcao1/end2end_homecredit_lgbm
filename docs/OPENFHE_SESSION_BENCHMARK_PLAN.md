# OpenFHE session and benchmark development plan

## Permanent rule

### User-facing benchmark contract

Benchmark commands must expose only workload concerns:

```text
function / operation
input dataset or prepared-data directory
value count or group count
repetitions
output directory / overwrite
```

They must **not** expose cryptographic tuning flags such as:

```text
ring dimension
multiplicative depth
first/scaling modulus sizes
plaintext-modulus bit count
scaling technique
key-switch technique
rotation indices
bootstrapping configuration
```

Those settings belong to `code/openfhe_direct/profiles.py`, the planner, and
the session backend. A benchmark calls a function; the backend selects its
reviewed profile and required keys. Do not add a cryptographic CLI flag as a
quick fix for a failing benchmark. Update and test the backend rule/profile,
then rerun the unchanged benchmark command.

The only allowed command-line layout controls are genuinely data-dependent
choices such as value/group count. Slot capacity, padding width, MIN/MAX
candidate width, and public normalization/range scale must be derived by the
client preparer or backend unless a separate research benchmark is explicitly
labelled as a parameter-sensitivity experiment.

This rule also applies to examples: business/application code must never pass
HE depth, modulus, ring, rescale, relinearization, or evaluation-key options.

### HE implementation boundary

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

The intended usage is split by role:

```python
workflow = HEWorkflow()
left = workflow.input("left")
right = workflow.input("right")
result = workflow.subtract(left, right)
workflow.output("result", result)

# Backend profile and planner choose HE parameters here.
runtime = workflow.compile(slot_count=prepared_layout.slot_count)

# Remote data owner only.
input_bundle = runtime.encrypt_inputs({"left": left_values, "right": right_values})

# Evaluator only: no secret key and no plaintext parents.
encrypted_results = runtime.evaluator.evaluate(input_bundle)

# Remote data owner only.
result = runtime.decrypt(encrypted_results["result"])
```

## Layer ownership

| Layer | Responsibility |
|---|---|
| `profiles.py` | Reviewed fixed HE backend parameters; never business or benchmark CLI options |
| `planner.py` | Inspect the complete DAG; derive depth and required evaluation keys before encryption |
| `api.py` | Public workflow plus separate client-encryption/evaluator/final-decryption roles |
| `session.py` | OpenFHE context, key, ciphertext, and operation implementation |
| Prepared-data modules | Numeric sanitation, masks, padding, and public group layout |
| Benchmark files | Select workload/data only, call the public API, record latency/accuracy, write reports |
| Example files | Show business calculations without cryptographic parameters |

## Package layout

```text
code/openfhe_direct/
├── profiles.py                # fixed reviewed backend profiles
├── planner.py                 # DAG depth and evaluation-key planning
├── api.py                     # client/evaluator application boundary
├── session.py                 # the only public HE implementation
├── prepared_data.py           # reusable client-side input loading
├── credit_rating_example.py
└── benchmarks/
    ├── api_latency.py
    ├── single_function.py
    ├── primitives.py
    ├── payment_diff_sum_mean.py
    ├── payment_diff_multigroup.py
    └── synthetic_vnd/
        ├── generate_dataset.py
        └── add.py
```

The `synthetic_vnd` package is a niche arithmetic test. It must not import or
call PAYMENT_DIFF, installments grouping, PSI, or credit-model code.

HEIR-generated MLIR/C++ runners under `code/heir/scripts/` are retained as
historical experiments. They are not the implementation path for new direct
OpenFHE-Python benchmarks.

## What benchmarks may do

Benchmark files may:

- load prepared numeric columns;
- request a planned runtime through the public API;
- call client encryption, evaluator calculation, and final client audit;
- measure setup, encryption, function, and audit latency;
- calculate a plaintext Python reference;
- decrypt only at the final accuracy boundary;
- write CSV, JSON, and Markdown reports.

Benchmark files must not:

- import `openfhe` directly;
- call `EvalAdd`, `EvalSub`, `EvalMult`, `EvalSum`, scheme-switching methods,
  or key-generation methods;
- configure CKKS/FHEW contexts directly;
- accept HE context/key/modulus/ring/depth options from their CLI;
- hard-code cryptographic parameters locally in the benchmark file;
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
| Real-data primitive matrix | `code/openfhe_direct/benchmarks/primitives.py`; calls only session methods | Complete |
| Raw-installments global SUM/MEAN | `code/openfhe_direct/benchmarks/payment_diff_sum_mean.py`; derives encrypted PAYMENT_DIFF and merges every ciphertext chunk | Complete |
| Population-backed multi-group benchmark | Selects from all prepared opaque groups, reassembles complete groups, then calls only `OpenFHECreditSession` methods | Complete |
| Synthetic VND BGV SUM | `benchmarks/synthetic_vnd/sum.py`; exact encrypted vector reduction with simple latency/accuracy report | Complete |
| Synthetic VND CKKS SUM | `benchmarks/synthetic_vnd/ckks_sum.py`; normalized approximate reduction over the same generated vector | Complete |

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
7. Add or update its rule in `planner.py` and reviewed defaults in
   `profiles.py`; do not add corresponding benchmark CLI flags.
8. Record setup requirements, scale/range contract, padding behavior, and
   whether scheme switching is used in the backend/report metadata.
9. Run a small server smoke test before increasing groups or row counts.

## Parameter-ownership migration status

| Item | Required final state | Status |
|---|---|---|
| CKKS ring/modulus/scaling defaults | `profiles.py` | In progress |
| DAG multiplicative depth | `planner.py` | Implemented for v1 operations |
| Evaluation-key selection | Planner → session | Implemented for v1 operations |
| Primitive benchmark HE flags | Removed from CLI | Pending |
| SUM/MEAN/VAR benchmark HE flags | Removed from CLI | Pending |
| Synthetic VND BGV/CKKS HE flags | Removed from CLI | Pending |
| MIN/MAX ring/range configuration | Backend derives from data/profile | Pending |
| Multi-group slot width/input scale | Client/backend derives automatically | Partially implemented |

Do not mark the benchmark migration complete or publish new run commands until
every normal benchmark command satisfies the user-facing contract above.

## Benchmark rollout

1. Primitive matrix over real parent columns:
   `code/openfhe_direct/benchmarks/primitives.py` calls `add`, `subtract`,
   `multiply`, `add_public_vector`, and `multiply_public_vector`. Each
   operation runs independently through `OpenFHECreditSession`; one shared
   context is reused within each row-count run.
2. Independent reductions:
   `code/openfhe_direct/benchmarks/payment_diff_sum_mean.py` reads the raw
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
- its normal CLI contains no cryptographic parameter knobs.

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

The synthetic VND benchmark additionally must:

- remain a separate, non-credit benchmark;
- generate deterministic aligned integer vector pairs for arbitrary lengths;
- default to inclusive values from 10,000,000 through 200,000,000;
- test one encrypted vector reduction through `OpenFHEBgvSession.sum()`;
- accept a plaintext-modulus bit budget and select the compatible prime internally;
- use depth zero and one 60-bit `FIXEDMANUAL` BGV first RNS prime for SUM;
- use NumPy `int64` SUM as the optimized plaintext reference;
- require the audited BGV result to equal the full vector SUM exactly;
- reject a vector whose total exceeds the centered plaintext-modulus capacity;
- report setup, NumPy, encryption, CT+CT evaluation, audit decryption,
  throughput, slowdown, MAE, maximum error, and pass/fail in a compact file;
- contain no direct OpenFHE `Eval*` call and no HEIR path.

The CKKS version uses the same generated vectors, normalizes values before
encryption, calls `OpenFHECreditSession.sum()`, restores VND only after final
audit decryption, and reports both absolute VND error and relative error. Its
default public normalization divisor is 1,000,000, and both accuracy
tolerances must pass.
