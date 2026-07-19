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

## Source-parity rule

- Every active workload must cite an expression that exists in
  `notebooks/lightgbm_with_simple_features.py`.
- A helper statistic such as `sum(x²)` is allowed only when it is required to
  reproduce a source operation such as `var`.
- Do not introduce a different model family or new business calculation into
  the active implementation plan.
- Ideas not present in the Python source must be labeled `Non-source idea` and
  remain outside implementation until separately approved.

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
| K01 `dot_product_ct_ct` | `sum(left * right)` over encrypted vectors | Source `size`/count and `sum` aggregations; active/closed and approved/refused filtered sums | MLIR/runner implemented; generated CKKS execution pending |
| K02 `moments` | Encrypted `count`, `sum(x)`, and helper `sum(x²)` | Reproduces source `mean` and `var` aggregations in bureau, previous applications, POS, installments, and credit cards | Planned; may initially compose K01 calls before a fused optimization |
| K03 `difference_moments` | Source `PAYMENT_DIFF = AMT_INSTALMENT - AMT_PAYMENT`, followed by its source `mean`, `sum`, and `var` | `installments_payments()` only | Planned |

Do not create separate kernels named `POS_COUNT`, `CC_COUNT`,
`active_credit_sum`, or `approved_application_sum`; those are workload
interpretations of K01.

## Source evidence for active kernels

| Kernel | Exact source operation |
|---|---|
| K01 | Bureau `sum` aggregations at lines 98-106; active/closed filters and aggregations at lines 115-125 |
| K01 | Previous-application `CNT_PAYMENT: sum` at line 154; approved/refused filters and aggregations at lines 163-171 |
| K01 | POS `size` and `POS_COUNT` at lines 182-193 |
| K01 | Installment `sum` aggregations and `INSTAL_COUNT` at lines 211-226 |
| K01 | Credit-card `sum` and `CC_COUNT` at lines 237-240 |
| K02 | Source `mean` and `var` aggregations throughout lines 81-111, 144-159, 182-188, 211-222 and 237; `sum(x²)` is only an implementation statistic for `var` |
| K03 | `PAYMENT_DIFF = AMT_INSTALMENT - AMT_PAYMENT` at line 204 and its `mean`, `sum`, `var` aggregation at line 216 |

Line references are to `notebooks/lightgbm_with_simple_features.py` in the
current repository. A changed source file requires this table to be audited
before adding another workload.

## Function coverage plan

| ID | Original function | HEIR code to build | Client-only flags — no HEIR code | Excluded/plaintext | Status |
|---|---|---|---|---|---|
| F01 | `application_train_test()` | None | CSV loading, sentinel/null handling, row filtering, factorization, one-hot encoding, and ratio features at lines 46-68 | General pandas preparation | Flag-only plan complete |
| F02 | `bureau_and_balance()` | Counts, sums, sums of squares, active/closed masked sums; mean/variance sufficient statistics | Cleaning, category masks, `SK_ID_BUREAU`/`SK_ID_CURR` alignment and grouping | `min`, `max`, exact encrypted division | Planned |
| F03 | `previous_applications()` | Counts, sums, sums of squares, approved/refused masked sums; mean/variance sufficient statistics | Sentinel/null handling, one-hot/status masks, grouping, `APP_CREDIT_PERC` | `min`, `max`, exact encrypted division | Planned |
| F04 | `pos_cash()` | `POS_COUNT`, sums, and mean sufficient statistics | One-hot masks and applicant row alignment | `max` | `POS_COUNT` prepare/report implemented; generated CKKS execution pending |
| F05 | `installments_payments()` | Source `PAYMENT_DIFF` plus its mean/sum/variance; counts and supported aggregates over client-prepared DPD/DBD | Null handling, `PAYMENT_PERC`, applicant grouping, and positive clipping at lines 199-226 | `min`, `max`, `nunique`, encrypted clipping/division | Planned |
| F06 | `credit_card_balance()` | Counts, sums, sums of squares, mean/variance sufficient statistics | One-hot encoding, dropped ID handling, applicant grouping | `min`, `max` | Planned |
| F07 | `kfold_lightgbm()` | None under current CKKS plan | None | Source LightGBM training/inference, KFold, ROC-AUC, feature importance, and plotting at lines 247-316 remain plaintext | Flag-only plan complete |

## Kernel-to-function benchmark mapping

| Function report | Function workload | Reusable kernel |
|---|---|---|
| `pos_cash/function_report.md` | `POS_COUNT` | K01 |
| `credit_card_balance/function_report.md` | `CC_COUNT` | K01 |
| `bureau_and_balance/function_report.md` | General sums and active/closed masked sums | K01; K02 for moments |
| `previous_applications/function_report.md` | General sums and approved/refused masked sums | K01; K02 for moments |
| `installments_payments/function_report.md` | `PAYMENT_DIFF` and supported aggregates | K03; K02 for prepared values |
| `credit_card_balance/function_report.md` | Supported numeric moments | K02 |

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

## Special non-source experiment lane

These experiments do not exist in `lightgbm_with_simple_features.py`. They are
kept because they help compare practical HE modeling options, but they must be
labeled `Special / non-source` in code, reports, and result summaries. They do
not count toward source-parity coverage.

| Special ID | Experiment | Purpose | Boundary/status |
|---|---|---|---|
| S01 | `linear_score_ct_pt` | Measure a CKKS-friendly weighted score over encrypted features | Special non-source benchmark; model must be trained or defined separately |
| S02 | `polynomial_score` | Measure a low-degree transformation of S01 output | Special non-source benchmark; not LightGBM or sigmoid-exact |
| S03 | `lightgbm_tree_inference` | Test whether a tiny exported LightGBM tree ensemble can be evaluated obliviously | Feasibility research only; not part of V1 or source-parity acceptance |

S03 must not claim direct LightGBM support. It requires plaintext training,
model export, fixed-tree MLIR generation, encrypted comparison/selection, and
oblivious evaluation of every retained path. Start with 1-3 shallow trees, not
the source configuration of up to 10,000 estimators.

## Implementation order

1. Complete the K01 synthetic kernel gate with HEIR-generated CKKS source.
2. Complete the separate `pos_cash()/POS_COUNT` function benchmark using K01.
3. Reuse the same K01 source/hash for `credit_card_balance()/CC_COUNT`, bureau
   active/closed sums, and previous approved/refused sums; keep separate reports.
4. Implement K02 moments and benchmark it separately under each source function.
5. Implement K03 and benchmark `installments_payments()/PAYMENT_DIFF`.
6. Produce a source-parity coverage report showing reproduced, client-only, and
   excluded outputs for every original function.

Special experiments run independently after the active source-derived kernels:

1. S01 linear score.
2. S02 polynomial score only if S01 requires an encrypted transformation.
3. S03 tiny LightGBM inference feasibility trial; stop before a full-model
   benchmark unless the tiny-tree accuracy and latency gates are acceptable.
