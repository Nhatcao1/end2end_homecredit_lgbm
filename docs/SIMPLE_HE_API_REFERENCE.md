# Simple HE API reference

This document describes the small public APIs currently exposed from
`code.heir.python_api`. It states what each API accepts, what it returns, and
what it deliberately does not support.

## 1. Which API should be used?

| API | Backend | Persistent ciphertexts | Current server |
|---|---|---:|---|
| `SourceBuiltCkksSession` | Source-built OpenFHE C++ | Yes | Recommended |
| `CkksSession` | Official OpenFHE Python wrapper | No | Python wrapper currently unavailable |
| `EncryptedDataset` | Official HEIR Python compiler and OpenFHE | Yes | Narrow binary-operation checkpoint API |

The three APIs use different ciphertext wrapper types. Their ciphertexts must
not be passed directly from one API to another.

For the current server, use:

```python
from code.heir.python_api import SourceBuiltCkksSession
```

## 2. Shared operation model

The simple session APIs expose these operations:

```python
he.add(left_ct, right_ct)       # encrypted column
he.subtract(left_ct, right_ct)  # encrypted column
he.multiply(left_ct, right_ct)  # encrypted column

he.sum(column_ct)               # encrypted scalar
he.mean(column_ct)              # encrypted scalar
he.variance(column_ct)          # encrypted scalar
he.minimum(column_ct)           # encrypted scalar
he.maximum(column_ct)           # encrypted scalar
```

Operation definitions:

| API | Encrypted calculation | Result |
|---|---|---|
| `add` | Element-wise CT + CT | Encrypted column |
| `subtract` | Element-wise CT − CT | Encrypted column |
| `multiply` | Element-wise CT × CT | Encrypted column |
| `sum` | Sum of valid lanes | Encrypted scalar |
| `mean` | Sum divided by public `valid_count` | Encrypted scalar |
| `variance` | Sample variance, equivalent to Pandas `var(ddof=1)` | Encrypted scalar |
| `minimum` | CKKS→FHEW comparison tree→CKKS | Encrypted scalar |
| `maximum` | CKKS→FHEW comparison tree→CKKS | Encrypted scalar |

SUM, MEAN, and VARIANCE apply a public validity mask so padding lanes are not
included. MIN and MAX use duplicate padding so padding does not introduce a
new extreme value.

The APIs do not decrypt between these operations. Decryption happens only
when `decrypt_column()` or `decrypt_scalar()` is explicitly called.

## 3. `SourceBuiltCkksSession`

Implementation:
`code/heir/python_api/source_built_session.py`.

This is the file-backed API for the current server. Python controls the
workflow, while a small C++ runner links against the OpenFHE installation
specified by `openfhe_dir`.

### 3.1 Create a session

```python
he = SourceBuiltCkksSession.create(
    checkpoint_dir=Path("encrypted_session"),
    width=128,
    input_scale=524288.0,
    ring_dimension=16384,
    openfhe_dir="/usr/local/lib/OpenFHE",
    overwrite=False,
)
```

| Parameter | Takes | Does not take |
|---|---|---|
| `checkpoint_dir` | A specific writable `Path` | A broad path such as `/` or the home directory |
| `width` | Power of two, at least 2 | Arbitrary non-power-of-two lane count |
| `input_scale` | Positive public numeric scale | NaN, infinity, zero, or a secret feature value |
| `ring_dimension` | Power of two and at least `2 × width` | A ring too small for the packed width |
| `openfhe_dir` | OpenFHE CMake package directory | A Python `openfhe` module |
| `overwrite` | Boolean | Partial/incremental merging with an old checkpoint |

Creation performs the following:

```text
write C++ runner source
→ CMake configure/build against OpenFHE
→ create CKKS context and keys
→ serialize context/public key/audit secret
→ write manifest with a context hash
```

It does not encrypt a column until `encrypt_column()` is called.

### 3.2 Reload a session

```python
he = SourceBuiltCkksSession.load(Path("encrypted_session"))
```

Takes:

- A checkpoint created by `SourceBuiltCkksSession.create()`.
- Its `manifest.json`, serialized context, and built runner.

Returns:

- A new Python session object pointing to the same serialized OpenFHE context.

Does not take:

- Parent plaintext values.
- A checkpoint created by `CkksSession` or `EncryptedDataset`.
- A context whose hash no longer matches the manifest.

### 3.3 Encrypt and name a parent column

```python
installment_ct = he.encrypt_column(
    installment_values,
    name="AMT_INSTALMENT",
)
```

Takes:

- A finite numeric sequence.
- Between 2 and `width` real values.
- A non-empty checkpoint name. Reusing a name replaces that named parent
  ciphertext and its manifest entry.
- Values satisfying `-0.5 < value / input_scale <= 0.5`.

Returns:

- `SourceBuiltEncryptedColumn`.
- A serialized ciphertext at
  `ciphertexts/<name>.ct`.
- A manifest entry containing scale, valid count, path, and SHA-256 hash.

Does not take:

- A Pandas DataFrame or a column name without its numeric values.
- Null, NaN, or infinity.
- More values than the configured width.
- Strings or categorical values.

### 3.4 Reload a named parent ciphertext

```python
installment_ct = he.load_column("AMT_INSTALMENT")
```

Takes:

- A parent column name registered by `encrypt_column()`.

Returns:

- A `SourceBuiltEncryptedColumn` file handle after validating its hash.

Does not take:

- A raw ciphertext path.
- A column from a different session.
- A derived ciphertext name. Derived outputs are currently not registered as
  named parent columns.

### 3.5 Binary column operations

```python
added_ct = he.add(left_ct, right_ct)
diff_ct = he.subtract(left_ct, right_ct)
product_ct = he.multiply(left_ct, right_ct)
```

Takes:

- Two `SourceBuiltEncryptedColumn` objects.
- Ciphertexts from the same session/context.
- The same public valid row count.
- For ADD/SUBTRACT, the same public scale.

Returns:

- A new `SourceBuiltEncryptedColumn`.
- A serialized derived ciphertext under `ciphertexts/derived/`.
- No plaintext value.

Does not take:

- Raw Python lists, Pandas Series, plaintext scalars, or filenames.
- Ciphertexts from different contexts.
- Columns with different valid counts.
- A business feature name such as `PAYMENT_DIFF`; the caller expresses that
  feature by calling `subtract()`.

For multiplication, the returned public output scale is
`left.scale × right.scale`.

### 3.6 Encrypted handle types

`SourceBuiltEncryptedColumn` exposes only public metadata:

| Field | Meaning |
|---|---|
| `path` | Serialized ciphertext path |
| `scale` | Public scale used to interpret the encrypted values |
| `valid_count` | Public number of real lanes |
| `session_fingerprint` | Context identity used to reject cross-session mixing |

`SourceBuiltEncryptedScalar` exposes `path`, `scale`, `source_count`, and
`session_fingerprint`. Neither type exposes plaintext values. Applications
should obtain these handles from session methods instead of constructing them
manually.

### 3.7 Reductions

```python
sum_ct = he.sum(column_ct)
mean_ct = he.mean(column_ct)
variance_ct = he.variance(column_ct)
minimum_ct = he.minimum(column_ct)
maximum_ct = he.maximum(column_ct)
```

Takes:

- One `SourceBuiltEncryptedColumn` from this session.
- Its public `valid_count`.

Returns:

- `SourceBuiltEncryptedScalar`.
- A serialized aggregate ciphertext.

Does not take:

- Raw values.
- A group identifier or a DataFrame groupby expression.
- A caller-provided divisor for MEAN.
- A `ddof` option for VARIANCE; the contract is fixed at sample variance
  `ddof=1`.
- An index/argmin/argmax output. Only encrypted MIN/MAX values are retained.

`sum()`, `mean()`, and `variance()` share one statistics execution when called
on the same column in the same Python process. `minimum()` and `maximum()`
similarly share one MIN/MAX execution.

### 3.8 Final decryption

```python
values = he.decrypt_column(diff_ct)
total = he.decrypt_scalar(sum_ct)
```

| API | Takes | Returns |
|---|---|---|
| `decrypt_column` | One compatible encrypted column | `tuple[float, ...]` containing valid lanes |
| `decrypt_scalar` | One compatible encrypted scalar | One `float` |

These methods do not accept ciphertexts from another session and are not
intended to run on an untrusted evaluator.

### 3.9 Source-built checkpoint contents

```text
encrypted_session/
├── manifest.json
├── public/
│   ├── context.bin
│   └── public.key
├── ciphertexts/
│   ├── AMT_INSTALMENT.ct
│   ├── AMT_PAYMENT.ct
│   ├── derived/
│   ├── aggregates/
│   └── minmax/
├── client_private/
│   ├── audit_secret.key
│   ├── inputs/
│   └── audits/
└── runner/
    ├── simple_ckks_session_runner.cpp
    └── build/simple_ckks_session_runner
```

The current trial stores the audit secret in `client_private/` because fresh
processes regenerate multiplication, rotation, and CKKS↔FHEW switching keys.
Do not give this directory to an untrusted evaluator.

## 4. `CkksSession`

Implementation: `code/heir/python_api/simple_session.py`.

This API has the same arithmetic and reduction method names, but holds
ciphertexts and the OpenFHE context in memory.

### 4.1 Creation

```python
he = CkksSession.create(
    width=128,
    input_scale=524288.0,
    ring_dimension=16384,
)
```

Takes:

- A power-of-two width of at least 2.
- A positive input scale.
- A compatible power-of-two ring dimension.
- An installed, compatible official OpenFHE Python wrapper.

Returns:

- An initialized in-memory `CkksSession`.

Does not take:

- A checkpoint directory.
- An OpenFHE CMake installation path.
- Existing serialized ciphertexts.

The current server has source-built OpenFHE but no compatible `openfhe` Python
module, so this backend is not the recommended server route.

Alternatively, `CkksSession(...)` may be constructed directly and followed by
one explicit `he.setup()` call. Calling `setup()` again is a no-op. Arithmetic,
encryption, and decryption reject a session that has not been set up.

### 4.2 In-memory types and operations

```python
column_ct = he.encrypt_column(values)
result_ct = he.subtract(left_ct, right_ct)
sum_ct = he.sum(result_ct)
result = he.decrypt_scalar(sum_ct)
```

| Type/API | Takes | Returns |
|---|---|---|
| `EncryptedColumn` | Created only by this session | Opaque in-memory encrypted vector |
| `EncryptedScalar` | Created by a reduction | Opaque one-value ciphertext |
| `encrypt_column` | 2 to `width` finite numeric values | `EncryptedColumn` |
| `add/subtract/multiply` | Two compatible `EncryptedColumn` objects | `EncryptedColumn` |
| `sum/mean/variance/minimum/maximum` | One compatible `EncryptedColumn` | `EncryptedScalar` |
| `decrypt_column` | `EncryptedColumn` from this session | Tuple of floats |
| `decrypt_scalar` | `EncryptedScalar` from this session | Float |

It does not save/load a session, survive process termination, accept a
DataFrame, or accept ciphertexts from a different `CkksSession`.

## 5. `EncryptedDataset`

Implementation: `code/heir/python_api/encrypted_dataset.py`.

This is the earlier narrow HEIR checkpoint API. It compiles exactly one binary
operation for exactly two named columns.

### 5.1 Encrypt

```python
dataset = EncryptedDataset.encrypt(
    {
        "AMT_INSTALMENT": installment_values,
        "AMT_PAYMENT": payment_values,
    },
    operation="subtract",
    width=128,
    input_scale=524288.0,
)
```

Takes:

- Exactly two uniquely named numeric columns.
- Equal row counts.
- `operation="add"`, `"subtract"`, or `"multiply"`.
- A compiled width large enough for all rows.
- Finite numeric values.

Returns:

- `EncryptedDataset` containing two named parent ciphertexts and one compiled
  HEIR binary program.

Does not take:

- One column or more than two columns.
- Unequal row counts.
- Null, NaN, infinity, strings, or categorical values.
- An aggregate such as SUM, MEAN, VARIANCE, MIN, or MAX.

### 5.2 Evaluate

```python
payment_diff_ct = dataset.evaluate(
    "AMT_INSTALMENT",
    "AMT_PAYMENT",
)
```

Takes:

- Two names already present in the dataset.

Returns:

- An encrypted result of the operation fixed when the dataset was created.

Does not take:

- An operation argument at evaluation time.
- A reduction.
- A ciphertext created by a simple session API.

### 5.3 Save and load

```python
dataset.save(
    Path("checkpoint"),
    include_audit_key=False,
)

evaluator_dataset = EncryptedDataset.load(Path("checkpoint"))
```

`save()` takes:

- A checkpoint path.
- Optional `include_audit_key=True` for a client-owned audit checkpoint.
- Optional `overwrite=True`.

`load()` takes:

- A matching saved manifest and artifacts.
- `for_audit=True` only when the checkpoint contains an audit key.

It validates:

- Artifact hashes.
- Context fingerprint.
- HEIR version.
- Recompiled circuit hash.
- Column order and operation.

It does not automatically migrate checkpoints across incompatible HEIR or
OpenFHE versions.

### 5.4 Decrypt

```python
audited_values = dataset.decrypt_result(payment_diff_ct)
```

This works only when loaded with `for_audit=True` from a checkpoint saved with
`include_audit_key=True`. An evaluator-only dataset intentionally cannot
decrypt.

## 6. Client preparation helpers

These helpers prepare public layout metadata and plaintext input before
encryption. They are not HE calculations.

### `public_power_of_two_scale(values)`

Takes:

- A non-empty finite numeric sequence.

Returns:

- A public power-of-two scale that places those values in
  `(-0.5, 0.5]` after normalization.

Does not encrypt data or prove that every future derived value fits the same
range. For subtraction used by MIN/MAX, the application currently doubles the
parent-only scale.

### `prepare_allowed_group_csv(...)`

Takes:

- An installments CSV path.
- One explicitly allowed `SK_ID_CURR`.
- A requested power-of-two bucket size, or `0` for automatic sizing.
- A maximum HE width.
- An output CSV path.

Returns:

- `PreparedAllowedGroup`.
- One complete, source-order-preserving, padded group CSV.
- Public validity mask, real count, and source-row metadata.

Does not:

- Encrypt anything.
- Run PSI.
- Prepare several IDs in one call.
- Truncate or split an oversized group.
- Keep rows whose parent numeric values are null or invalid.

### `load_prepared_allowed_group(path)`

Takes:

- One prepared group CSV with contiguous lanes and a 1/0 validity mask.

Returns:

- A validated `PreparedAllowedGroup`.

Does not accept an arbitrary raw installments CSV or multiple groups.

## 7. What the simple APIs do not provide

The current simple APIs are deliberately not encrypted Pandas.

They do not currently provide:

- `EncryptedDataFrame` or `df["column"]` syntax.
- General `groupby`.
- Multi-group scheduling in one API call.
- Joins or PSI.
- Null handling after encryption.
- Categorical encoding or `nunique`.
- Division or reciprocal as a general API.
- Arbitrary encrypted comparisons or boolean filters.
- Argmin or argmax retention.
- Automatic bootstrapping.
- Automatic scale/depth selection for an arbitrary pipeline.
- LightGBM inference.
- Compatibility between ciphertext wrapper types from different backends.

Group selection, null removal, PSI, sorting, and padding remain client/data
preparation responsibilities. Benchmark and application runners may compose
those steps around the simple HE API, but they are not hidden inside its
arithmetic methods.

## 8. Current security qualification

The source-built trial prioritizes functionality and reproducibility:

- CKKS security enforcement is currently disabled with `HEStd_NotSet`.
- CKKS↔FHEW MIN/MAX currently uses the OpenFHE `TOY` FHEW security setting.
- The checkpoint contains a client audit secret.
- Evaluation-key regeneration currently occurs in fresh local processes.

Therefore this API is suitable for development and correctness testing, not a
production 128-bit security claim. A production design must choose validated
parameters, generate evaluation/switching keys on the client side, and give
the evaluator only public context, public/evaluation keys, and ciphertexts.

## 9. Runnable examples

In-memory API, when a compatible OpenFHE Python wrapper is installed:

```bash
python3 code/heir/examples/simple_ciphertext_api.py
```

Current source-built server API with a parent checkpoint and fresh-process
reload:

```bash
python3 code/heir/examples/payment_diff_simple_api_e2e.py \
  --stage roundtrip \
  --installments data/home_credit/installments_payments.csv \
  --allowed-sk-id-curr 100001 \
  --ring-dimension 16384 \
  --openfhe-dir /usr/local/lib/OpenFHE \
  --output-dir benchmark_runs/payment_diff_simple_api_100001 \
  --overwrite
```

Earlier HEIR `EncryptedDataset` checkpoint example:

```bash
python3 code/heir/examples/encrypted_dataset_save_load.py \
  --checkpoint-dir benchmark_runs/encrypted_dataset_trial \
  --overwrite
```
