# Function-by-function HEIR benchmark plan

The original Python functions are the preparation, benchmark, and reporting
units. Reusable arithmetic kernels are the HEIR development units. This keeps
business traceability without generating duplicate cryptographic code.

## Two-layer architecture

```text
Function workload
  -> trusted preparation and plaintext reference
  -> select reusable kernel and pack fixed-size tensors
  -> HEIR-generated CKKS/OpenFHE execution
  -> function-specific comparison and Markdown report
```

### Kernel layer

- Owns MLIR, generated-source validation, CKKS parameters, chunking, and
  synthetic correctness tests.
- Has no Home Credit table names or business labels.
- A kernel is generated and cryptographically validated once, then referenced
  by multiple function workloads using its ID and source hash.

### Function workload layer

- Owns table reading, trusted alignment/grouping, masks, plaintext references,
  tensor interpretation, and real-data benchmarks.
- Produces a separate report even when another function uses the same kernel.
- Records the kernel ID, generated-source hash, vector size, CKKS parameters,
  input scope, privacy boundary, errors, timings, and artifact sizes.

## Decision labels

| Label | Meaning | Code policy |
|---|---|---|
| `HEIR exact` | Fixed CKKS arithmetic can reproduce the operation | Implement and benchmark |
| `HEIR statistics` | CKKS returns sufficient statistics; trusted client finishes division or square root | Implement only the encrypted statistics |
| `Client only` | The data owner can prepare it more safely and cheaply before encryption | Flag in the report; do not write HEIR code |
| `Excluded V1` | Comparison, uniqueness, or tree logic is a poor exact-CKKS target | Do not implement in the initial benchmark lane |
| `Plaintext ML` | Training, validation, or reporting does not benefit from HE | Keep outside HEIR |

## Two acceptance gates

### Reusable-kernel gate

1. The kernel has a fixed numeric input/output contract and contains no
   function-specific preparation.
2. Synthetic tests cover normal values, zero vectors, padding, chunk boundaries,
   and declared CKKS error tolerance.
3. HEIR-generated CKKS/OpenFHE source is compiled and executed; handwritten
   OpenFHE does not count as HEIR completion.
4. Generated-source hashes are saved and BGV, BFV, and BinFHE output is rejected.
5. Vector size, multiplicative depth, scale/level assumptions, and required
   evaluation keys are recorded.

### Function-benchmark gate

1. Record the original source operation and exact input scope.
2. Keep raw identifiers, strings, nulls, and `TARGET` out of HE tensors unless
   a workload explicitly requires the target mask.
3. Separate trusted preparation, encrypted calculation, and trusted
   post-processing in the report.
4. Reference a reusable kernel by ID, source hash, vector size, and CKKS
   parameters rather than copying its generated code.
5. Compare decrypted results with the plaintext reference under a declared
   error tolerance.
6. Report preparation, encryption, evaluation, decryption, padding/chunking,
   and artifact sizes.

## Reusable kernel registry

| Kernel ID | Contract | Function uses | Status |
|---|---|---|---|
| K01 `dot_product_ct_ct` | `sum(left * right)` over encrypted vectors | Counts, sums, active/closed and approved/refused masked sums, current `POS_COUNT` | MLIR/runner implemented; generated CKKS execution pending |
| K02 `moments` | Encrypted `count`, `sum(x)`, and `sum(x²)` | Bureau, previous applications, POS, installments, and credit-card sufficient statistics | Planned; may initially compose K01 calls before a fused optimization |
| K03 `difference_moments` | Moments of encrypted `left - right` | Installment `PAYMENT_DIFF` and unclipped DPD/DBD arithmetic | Planned |
| K04 `linear_score_ct_pt` | `bias + sum(encrypted_feature * plaintext_weight)` | Selected applicant risk score | Planned |
| K05 `polynomial_score` | Low-degree polynomial over an encrypted linear score | Optional encrypted score transformation | Optional after K04 |

Do not create separate kernels named `POS_COUNT`, `CC_COUNT`,
`active_credit_sum`, or `approved_application_sum`; those are workload
interpretations of K01.

## Function coverage plan

| ID | Original function | HEIR code to build | Client-only flags — no HEIR code | Excluded/plaintext | Status |
|---|---|---|---|---|---|
| F01 | `application_train_test()` | No standalone V1 feature kernel; its selected numeric output will feed encrypted scoring | CSV loading, sentinel/null handling, row filtering, factorization, one-hot encoding, all ratio features | General pandas preparation | Flag-only plan complete |
| F02 | `bureau_and_balance()` | Counts, sums, sums of squares, active/closed masked sums; mean/variance sufficient statistics | Cleaning, category masks, `SK_ID_BUREAU`/`SK_ID_CURR` alignment and grouping | `min`, `max`, exact encrypted division | Planned |
| F03 | `previous_applications()` | Counts, sums, sums of squares, approved/refused masked sums; mean/variance sufficient statistics | Sentinel/null handling, one-hot/status masks, grouping, `APP_CREDIT_PERC` | `min`, `max`, exact encrypted division | Planned |
| F04 | `pos_cash()` | `POS_COUNT`, sums, and mean sufficient statistics | One-hot masks and applicant row alignment | `max` | `POS_COUNT` prepare/report implemented; generated CKKS execution pending |
| F05 | `installments_payments()` | `PAYMENT_DIFF`, arithmetic DPD/DBD differences, counts, sums, sum of squares | Null handling, `PAYMENT_PERC`, applicant grouping, positive-clipping masks/values | `min`, `max`, `nunique`, encrypted clipping/division | Planned |
| F06 | `credit_card_balance()` | Counts, sums, sums of squares, mean/variance sufficient statistics | One-hot encoding, dropped ID handling, applicant grouping | `min`, `max` | Planned |
| F07 | `kfold_lightgbm()` | Separate selected-feature linear/polynomial inference benchmark only | Train/test split and prepared feature-vector encryption | LightGBM training/inference, KFold, ROC-AUC, feature importance and plotting | Planned scoring lane; original function remains plaintext |

## Kernel-to-function benchmark mapping

| Function report | Function workload | Reusable kernel |
|---|---|---|
| `pos_cash/function_report.md` | `POS_COUNT` | K01 |
| `credit_card_balance/function_report.md` | `CC_COUNT` | K01 |
| `bureau_and_balance/function_report.md` | General sums and active/closed masked sums | K01; K02 for moments |
| `previous_applications/function_report.md` | General sums and approved/refused masked sums | K01; K02 for moments |
| `installments_payments/function_report.md` | `PAYMENT_DIFF` and supported aggregates | K03; K02 for prepared values |
| `credit_card_balance/function_report.md` | Supported numeric moments | K02 |
| `scoring/function_report.md` | Selected-feature linear score | K04; optionally K05 |

Function benchmark output remains separate:

```text
benchmark_runs/
├── pos_cash/pos_count/<run-name>/benchmark_report.md
├── credit_card_balance/credit_card_count/<run-name>/benchmark_report.md
├── bureau_and_balance/active_aggregates/<run-name>/benchmark_report.md
└── previous_applications/approved_aggregates/<run-name>/benchmark_report.md
```

## Explicit no-code decisions

### Missing values

Missing-value processing is `Client only`. CKKS cannot consume a pandas `NaN`
as business semantics. The trusted data owner should choose an imputation or
sentinel policy, optionally add an ordinary 0/1 missing-indicator feature, and
then encrypt the prepared numeric vector.

An encrypted missingness mask is useful only for a different use case: an
untrusted analytics server calculating private missing-rate/data-quality
statistics. That is not required for this end-to-end scoring pipeline, so no
missing-count HEIR workload will be implemented here.

### Other client-only work

- Raw strings, one-hot encoding, category discovery, and category normalization.
- Row filtering and abnormal-value replacement.
- Dynamic joins and group discovery by raw identifiers.
- Ratios whose numerator and denominator are both available to the trusted
  client; compute the ratio before encryption.
- Plotting, report formatting, model training, and model validation.

### Excluded V1 work

- Exact `min`, `max`, positive clipping, sorting, and threshold comparison.
- Exact `nunique`.
- Encrypted LightGBM training or tree traversal.
- Encrypted division merely to reproduce a value the client can prepare before
  encryption.

## Implementation order

1. Complete the K01 synthetic kernel gate with HEIR-generated CKKS source.
2. Complete the separate `pos_cash()/POS_COUNT` function benchmark using K01.
3. Reuse the same K01 source/hash for `credit_card_balance()/CC_COUNT`, bureau
   active/closed sums, and previous approved/refused sums; keep separate reports.
4. Implement K02 moments and benchmark it separately under each source function.
5. Implement K03 and benchmark `installments_payments()/PAYMENT_DIFF`.
6. Assemble the selected applicant feature vector from supported function outputs.
7. Implement K04 and benchmark the selected linear score.
8. Add K05 only if an encrypted score transformation is required.
