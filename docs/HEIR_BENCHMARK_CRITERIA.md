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
- Produces one report for each complete original function. Internal general,
  branch, count, and moment components share one source scan and report.
- Records the kernel ID, generated-source hash, vector size, CKKS parameters,
  input scope, privacy boundary, errors, timings, and artifact sizes.

## Decision labels

| Label | Meaning | Code policy |
|---|---|---|
| `HEIR exact` | Fixed CKKS arithmetic can reproduce the operation | Implement and benchmark |
| `HEIR statistics` | CKKS returns encrypted sufficient statistics | Keep ciphertext outputs; encrypted mean/variance finalization is a separate pending continuation gate |
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
| K01 `dot_product_ct_ct` | `sum(left * right)` over encrypted vectors | Source `size`/count and `sum` aggregations; active/closed and approved/refused filtered sums | Contract, MLIR, oracle, and generated-source runner implemented; CKKS execution pending |
| K02 `moments` | Encrypted `count`, `sum(x)`, and helper `sum(x²)` | Reproduces source `mean` and `var` aggregations in bureau, previous applications, POS, installments, and credit cards | Contract, fused MLIR, and plaintext oracle implemented; generated-source runner pending |
| K03 `difference_moments` | Source `PAYMENT_DIFF = AMT_INSTALMENT - AMT_PAYMENT`, followed by its source `mean`, `sum`, and `var` | `installments_payments()` only | Contract, fused MLIR, and plaintext oracle implemented; generated-source runner pending |

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
| F02 | `bureau_and_balance()` | Counts, sums, sums of squares, active/closed masked sums; mean/variance sufficient statistics | Cleaning, category masks, `SK_ID_BUREAU`/`SK_ID_CURR` alignment and grouping | `min`, `max`, exact encrypted division | One combined BUREAU preparation/oracle/report implemented; generated CKKS pending |
| F03 | `previous_applications()` | Counts, sums, sums of squares, approved/refused masked sums; mean/variance sufficient statistics | Sentinel/null handling, one-hot/status masks, grouping, `APP_CREDIT_PERC` | `min`, `max`, exact encrypted division | One combined PREVIOUS preparation/oracle/report implemented; generated CKKS pending |
| F04 | `pos_cash()` | `POS_COUNT`, sums, and mean sufficient statistics | One-hot masks and applicant row alignment | `max` | One combined POS preparation/oracle/report implemented; K01 generated runner exists, CKKS execution pending |
| F05 | `installments_payments()` | Source `PAYMENT_DIFF` plus its mean/sum/variance; counts and supported aggregates over client-prepared DPD/DBD | Null handling, `PAYMENT_PERC`, applicant grouping, and positive clipping at lines 199-226 | `min`, `max`, `nunique`, encrypted clipping/division | One combined INSTALLMENTS preparation/oracle/report implemented; generated CKKS pending |
| F06 | `credit_card_balance()` | Counts, sums, sums of squares, mean/variance sufficient statistics | One-hot encoding, dropped ID handling, applicant grouping | `min`, `max` | One combined CREDIT_CARD preparation/oracle/report implemented; generated CKKS pending |
| F07 | `kfold_lightgbm()` | None under current CKKS plan | None | Source LightGBM training/inference, KFold, ROC-AUC, feature importance, and plotting at lines 247-316 remain plaintext | Flag-only plan complete |

## Complete function benchmark mapping

| Public benchmark | Internal source components in one report | Reusable kernel |
|---|---|---|
| `bureau` | General, active, and closed bureau aggregates | K01, K02 |
| `previous` | General, approved, and refused previous-application aggregates | K01, K02 |
| `pos` | POS count and supported numeric means | K01, K02 |
| `installments` | Count, client-prepared aggregates, and `PAYMENT_DIFF` moments | K01, K02, K03 |
| `credit_card` | Credit-card count and supported numeric moments | K01, K02 |

## Internal computation trace catalog

The IDs below label sections inside the five complete reports. They preserve
source traceability but are not public or independently executed benchmarks.

| Trace ID | Internal computation component | Reusable kernel(s) | Status inside combined function |
|---|---|---|---|
| B01-B03 | Bureau general/active/closed | K01, K02 | Prepared together in BUREAU |
| P01-P03 | Previous general/approved/refused | K01, K02 | Prepared together in PREVIOUS |
| POS01-POS02 | POS count/numeric means | K01, K02 | Prepared together in POS |
| I01-I03 | Installment count/prepared/difference | K01, K02, K03 | Prepared together in INSTALLMENTS |
| C01-C02 | Credit-card count/numeric moments | K01, K02 | Prepared together in CREDIT_CARD |

The current adapters cover source numeric columns. Dynamic category discovery
and one-hot encoding remain client-only; category-mean benchmarks can be added
later only against a client-frozen numeric category schema.

### Historical adapter validation snapshot

On 2026-07-19, all 13 adapters completed a bounded real-data preparation run
using eight applicants and at most 500,000 rows from each source/auxiliary CSV.
Thirteen separate Markdown reports were produced. Eleven task reports contained
matched source rows; the two credit-card reports correctly exercised their
zero-history/padding path within that bounded slice. This snapshot validates
schema handling and report generation only and remains `prepared_only`.

The public interface has since been consolidated into five function benchmarks;
automated tests verify that every function scans its main source once and still
reproduces all internal branch/count/transform assertions.

On 2026-07-19, the consolidated `bureau` smoke run selected eight applicants,
scanned 500,000 bureau rows once, matched two rows, and prepared B01, B02, and
B03 in one report. This is a preparation/report integration check, not CKKS
execution evidence.

Function benchmark output remains separate:

```text
benchmark_runs/
├── functions/bureau_and_balance/<run-name>/benchmark_report.md
├── functions/previous_applications/<run-name>/benchmark_report.md
├── functions/pos_cash/<run-name>/benchmark_report.md
├── functions/installments_payments/<run-name>/benchmark_report.md
└── functions/credit_card_balance/<run-name>/benchmark_report.md
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
| S01 | `linear_score_ct_pt` | Measure a CKKS-friendly weighted score over encrypted features | Contract, MLIR, and oracle implemented; special non-source; generated-source runner pending |
| S02 | `polynomial_score` | Measure a low-degree transformation of S01 output | Contract, Horner MLIR, and oracle implemented; special non-source; generated-source runner pending |
| S03 | `lightgbm_tree_inference` | Test whether a tiny exported LightGBM tree ensemble can be evaluated obliviously | Deferred by decision; documented only, with no implementation or registry entry |

S03 must not claim direct LightGBM support. It is not part of the reusable
arithmetic layer and currently has no code. A future implementation would
require plaintext training,
model export, fixed-tree MLIR generation, encrypted comparison/selection, and
oblivious evaluation of every retained path. Start with 1-3 shallow trees, not
the source configuration of up to 10,000 estimators.

## Implementation order

The reusable arithmetic MLIR/oracle layer is complete for K01-K03 and S01-S02,
and five combined function preparation/report paths are implemented. The
remaining order is:

1. Complete the K01 synthetic kernel gate with HEIR-generated CKKS source.
2. Generalize the K01 generated runner for count and masked-sum components.
3. Generate and validate K02 CKKS/OpenFHE source once, then reuse its source hash
   across every K02 function report.
4. Generate and validate K03 CKKS/OpenFHE source for payment differences.
5. Add encrypted mean/variance finalization so sufficient statistics remain
   ciphertext through the pipeline.
6. Serialize function ciphertext outputs and populate the bundle manifests with
   context/key/layout/scale/level metadata.
7. Execute each complete function against its kernels and populate accuracy,
   timing, CKKS parameters, evaluation keys, and artifact sizes.
8. Produce a source-parity coverage report showing reproduced, client-only, and
   excluded outputs for every original function.

Special experiments run independently after the active source-derived kernels:

1. S01 linear score.
2. S02 polynomial score only if S01 requires an encrypted transformation.
3. S03 tiny LightGBM inference feasibility trial; stop before a full-model
   benchmark unless the tiny-tree accuracy and latency gates are acceptable.
