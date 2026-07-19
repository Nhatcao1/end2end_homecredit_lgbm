# Function-by-function HEIR benchmark plan

The original Python functions are the planning units. Each function is split
into small arithmetic kernels because one pandas function can contain both
HE-friendly arithmetic and operations that should remain on the client.

## Decision labels

| Label | Meaning | Code policy |
|---|---|---|
| `HEIR exact` | Fixed CKKS arithmetic can reproduce the operation | Implement and benchmark |
| `HEIR statistics` | CKKS returns sufficient statistics; trusted client finishes division or square root | Implement only the encrypted statistics |
| `Client only` | The data owner can prepare it more safely and cheaply before encryption | Flag in the report; do not write HEIR code |
| `Excluded V1` | Comparison, uniqueness, or tree logic is a poor exact-CKKS target | Do not implement in the initial benchmark lane |
| `Plaintext ML` | Training, validation, or reporting does not benefit from HE | Keep outside HEIR |

## Common HEIR acceptance criteria

1. Record the original source operation and exact input scope.
2. Keep raw identifiers, strings, nulls, and `TARGET` out of HE tensors unless
   a workload explicitly requires the target mask.
3. Separate trusted preparation, encrypted calculation, and trusted
   post-processing in the report.
4. Compile and execute HEIR-generated CKKS/OpenFHE source; handwritten OpenFHE
   does not count as HEIR completion.
5. Save generated-source hashes and reject BGV, BFV, and BinFHE output.
6. Compare decrypted results with the plaintext reference under a declared
   error tolerance.
7. Report preparation, encryption, evaluation, decryption, and artifact sizes.

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

1. Complete `pos_cash()/POS_COUNT` with generated CKKS source.
2. Reuse the count kernel for `credit_card_balance()/CC_COUNT`.
3. Add `installments_payments()/PAYMENT_DIFF` plus count and sum kernels.
4. Add bureau active/closed masked sums.
5. Add previous-application approved/refused masked sums.
6. Assemble the selected applicant feature vector.
7. Benchmark linear and optional low-degree polynomial scoring.
