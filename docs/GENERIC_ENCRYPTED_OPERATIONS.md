# Generic encrypted-column operations

## Rule

The client may turn null/non-finite values into a numeric value plus a validity
mask, encode categories, and pack fixed-size vectors. It must not calculate a
source feature expression before encryption. Feature arithmetic runs over
encrypted columns.

## API boundary

```text
nullable input columns
  -> client value + validity-mask packing
  -> encrypt
  -> encrypted_binary(add | subtract | multiply)
  -> encrypted reductions (count | sum | sum_squares)
  -> decrypt only for the client accuracy audit
```

`prepare_nullable_column()` is deliberately limited to input representation. It
does not derive ratios, differences, means, or other business features.

## Capability matrix

| Python expression | Route | Status |
|---|---|---|
| `a + b`, `a - b`, `a * b` | native CKKS | Implemented generic API |
| `sum` | native CKKS packed reduction | Implemented generic fixed-public-count kernel |
| `mean`, sample variance | native CKKS packed reduction | Implemented when group count is public and fixed |
| `a / b` | reciprocal polynomial + multiply | Implemented with a bounded positive-denominator contract |
| `x > threshold` | OpenFHE CKKS↔FHEW sign comparison | Executable standalone benchmark; exact equality remains excluded by a public margin contract |
| `min` / `max` | OpenFHE CKKS-to-FHEW switching | Separate session required; not emitted by HEIR |

## Representative source benchmarks

We deliberately test a compact set of source expressions rather than treating
every original feature function as a separate HE program.

| Benchmark | Original source expression | Current status | Why it is included |
|---|---|---|---|
| `installments_payment_diff` | `AMT_INSTALMENT - AMT_PAYMENT` | Executable exact CKKS | Validates raw two-column arithmetic, null packing, HEIR lowering, timing, and decryption accuracy. |
| `application_days_employed_perc` | `DAYS_EMPLOYED / DAYS_BIRTH` | Deferred | Validates the required reciprocal/division design and source sentinel-to-null handling. |
| `installments_dpd_clip` | `max(DAYS_ENTRY_PAYMENT - DAYS_INSTALMENT, 0)` | Deferred | Covers comparison/clipping, which must remain visibly separate from ordinary CKKS arithmetic. |

## Exact ordered comparison: separate CKKS↔FHEW benchmark

`<`, `>`, threshold rules, and later min/max require an ordered predicate.
They are **not** native CKKS operations. The following benchmark uses
OpenFHE's CKKS-to-FHEW sign operation and returns an encrypted CKKS sign result
for both encrypted-column comparison and a public-threshold rule. It writes a
lane-level decrypted audit only to establish correctness; its two result
ciphertexts stay encrypted.

```bash
python3 code/heir/scripts/run_ckks_fhew_comparison_benchmark.py \
  --output-dir benchmark_runs/ckks_fhew_comparison_01 \
  --overwrite \
  --openfhe-dir /usr/local/lib/OpenFHE
```

Read `REPORT.md` and `comparison_audit.csv` in that output directory. The
default is four deliberately non-equal test lanes. To test four owner-provided
values, provide two one-column `value` CSV files:

```bash
python3 code/heir/scripts/run_ckks_fhew_comparison_benchmark.py \
  --output-dir benchmark_runs/ckks_fhew_comparison_custom_01 \
  --overwrite \
  --left-csv /path/to/left.csv \
  --right-csv /path/to/right.csv \
  --threshold 0 \
  --minimum-margin 0.25 \
  --openfhe-dir /usr/local/lib/OpenFHE
```

The `minimum-margin` is a public numerical contract, not a secret-data check:
the evaluator must reject exact equality/near-zero candidates or use a separate
equality/tolerance design. These scheme-switching ciphertexts belong to their
own OpenFHE session and cannot be mixed directly with ordinary HEIR CKKS
ciphertexts.

## Literal encrypted min/max: separate reduction benchmark

Min/max is not a threshold predicate. It reduces one encrypted vector through
an encrypted comparison-and-selection tree. The benchmark loads a **real
sanitized parent column** from the prepared installments batches—for example
100 or 1,000 `AMT_PAYMENT` values—and normalizes it into the OpenFHE
unit-circle contract during CKKS encoding. A non-power-of-two count is padded
by repeating a genuine candidate: 100 values become 128 encrypted lanes; 1,000
become 1,024. It produces encrypted minimum and maximum ciphertexts, then
decrypts them solely for the Python accuracy report.

Create the prepared source once if it is not already present:

```bash
python3 code/heir/scripts/prepare_full_installments_columns.py \
  --input-csv data/home_credit/installments_payments.csv \
  --output-dir data/prepared/installments_columns \
  --vector-size 8192 \
  --overwrite
```

```bash
python3 code/heir/scripts/run_ckks_fhew_minmax_benchmark.py \
  --output-dir benchmark_runs/ckks_fhew_minmax_01 \
  --overwrite \
  --value-count 100 \
  --openfhe-dir /usr/local/lib/OpenFHE
```

For the 1,000-candidate run, change only `--value-count 1000`; it encrypts
1,024 lanes. By default it reads
`data/prepared/installments_columns/batches/batch_000000.csv` and the
`AMT_PAYMENT` column. Use `--column AMT_INSTALMENT` to select the other prepared
parent column, or pass `--input-csv /path/to/column.csv` for a different real
one-column source. `--input-scale 0` (the default) records an auto-selected
public power-of-two encoding scale based on the loaded values; provide a
positive scale when using a fixed public schema range. The report is
`benchmark_runs/ckks_fhew_minmax_01/REPORT.md`. Ties are valid for min/max;
only the discarded argmin/argmax identity is non-unique. This is a
single-encrypted-vector capability/accuracy benchmark, not a claim that min/max
is ready for a full 13-million-row column or a grouped reduction.

After the arithmetic benchmark, encrypted mask combination and masked grouped
reductions use the same generic `multiply` / `count_sum_squares` contracts.
`min`, `max`, and `nunique` are not silently included in this first lane.

Generate and run the exact first benchmark on the HEIR/OpenFHE server:

```bash
python3 code/heir/scripts/generate_generic_binary_ckks.py \
  --operation subtract \
  --vector-size 8192 \
  --output-dir benchmark_runs/generated_ckks/generic_subtract_8192

python3 code/heir/scripts/run_representative_benchmarks.py \
  --benchmark installments_payment_diff \
  --backend heir-generated-ckks \
  --data-dir data/home_credit \
  --row-limit 8192 \
  --vector-size 8192 \
  --generated-dir benchmark_runs/generated_ckks/generic_subtract_8192 \
  --openfhe-dir /usr/local/lib/OpenFHE \
  --run-name payment_diff_8192_01
```

## Tiny review example: `PAYMENT_PERC`, `DPD`, and `DBD`

For an executable three-row review example, use one command. It generates and
keeps the MLIR internally, then lowers, translates, compiles, encrypts,
evaluates, decrypts for audit, and writes one result table:

```bash
python3 code/heir/scripts/run_quick_installments_heir_demo.py \
  --output-dir benchmark_runs/quick_installments_heir_01 \
  --overwrite \
  --vector-size 8 \
  --openfhe-dir /usr/local/lib/OpenFHE
```

Read `result_table.csv` or `quick_demo_report.md`; do not manually run MLIR
files. The generated MLIR remains under `payment_perc/` and
`positive_difference/` for review. `payment_perc_newton.mlir` shows a two-step
reciprocal approximation entirely after encryption. The public scale is a
representation/range policy; it does not calculate a feature client-side.
`positive_difference_smoothstep.mlir` is one generic ordered operation: use
entry-payment minus installment for DPD, and reverse inputs for DBD. It is
approximate near zero. Exact clipping is still the separate OpenFHE CKKS-to-
FHEW comparison experiment.

## First ciphertext proof: `PAYMENT_PERC` and `PAYMENT_DIFF`

### Required precision probe: `PAYMENT_PERC` before aggregation

`PAYMENT_PERC` is a reciprocal approximation, so it must be validated alone
before it is passed to any aggregation kernel. The dedicated probe requests a
CKKS multiplicative-depth budget explicitly and records the parameter calls
emitted by HEIR/OpenFHE in `result.json`. The server's installed HEIR release
does not expose `mul-depth` through its `scheme-to-openfhe` pipeline, so this
probe patches only the translated OpenFHE `SetMultiplicativeDepth(...)` call
before compilation and records both the inferred and requested values. It
deliberately does not run sum.

```bash
python3 code/heir/scripts/run_payment_perc_depth_probe.py \
  --output-dir benchmark_runs/payment_perc_depth12_01 \
  --overwrite \
  --vector-size 8 \
  --ckks-mul-depth 12 \
  --openfhe-dir /usr/local/lib/OpenFHE
```

The client only prepares a safe denominator and encrypted validity flag:
missing, non-positive, or out-of-range installment values are replaced with the
public scale before encryption, and the encrypted result is masked afterward.
It never computes the ratio client-side. The current public contract is
`AMT_INSTALMENT / 1000` in `[0.5, 1.0]`; change and validate that contract for
real data before using this kernel.

This ratio path is intentionally evaluated as a feature-only proof first. In
the current small-server environment, taking its output into `sum`, `mean`, or
`var` exhausted the available CKKS depth/memory. Those aggregate results must
therefore remain **not run**, rather than borrowing the plaintext answer. The
feature evaluation time and accuracy remain useful evidence for the generic
bounded-ratio kernel.

The older combined `run_payment_features_ciphertext_demo.py` experiment is
retained as a troubleshooting artifact only. Do not use it as the benchmark
result: it attempted to continue the deep ratio ciphertext into aggregation and
therefore mixed a valid feature proof with an unstable chained path. Use the
single report-producing benchmark below instead.

### First encrypted mean and variance chain

The general grouped path uses exact `PAYMENT_DIFF` so statistics infrastructure
can be checked independently of reciprocal-feature noise. One encrypted feature
vector is consumed once to produce encrypted `count`, `sum`, and `sum_squares`.
The finalizer then consumes those encrypted scalars to produce encrypted mean
and sample variance. It is a bootstrap experiment on the current HEIR build;
the small server was OOM-killed during bootstrap setup/key generation. It is
kept for a larger-machine experiment, not presented as a passed ordinary CKKS
benchmark.

```bash
python3 code/heir/scripts/run_payment_diff_moments_demo.py \
  --output-dir benchmark_runs/payment_diff_moments_01 \
  --overwrite \
  --vector-size 8 \
  --ckks-mul-depth 24 \
  --openfhe-dir /usr/local/lib/OpenFHE
```

If it completes on a sufficiently large server, review `feature_comparison.csv`
and `statistics_comparison.csv`. Ciphertexts for the source feature, count,
sum, squared sum, mean, and variance remain in `ciphertexts/`. Because HEIR
inserts a bootstrap for this finalizer, the finalizer creates the shared context
and bootstrap keys before the shallower feature and moments stages are
configured.

### Small executable `PAYMENT_DIFF` aggregation proof

For the three fixed review rows, use the bounded non-bootstrap runner below.
It encrypts `AMT_INSTALMENT` and `AMT_PAYMENT`, derives
`PAYMENT_DIFF = AMT_INSTALMENT - AMT_PAYMENT` after encryption, serializes that
source ciphertext. It then runs separately isolated `sum`, `mean`, and sample
`var` processes. Each process reloads the same one-time CKKS session and only
its input artifact. This is intentional: the installed generated HEIR code may
mutate an input/output buffer in place, so multi-output results must not share
a mutable ciphertext object. It writes a familiar audit table:

```bash
python3 code/heir/scripts/run_payment_diff_fixed_count_aggregates.py \
  --output-dir benchmark_runs/payment_diff_fixed_count_aggregates \
  --overwrite \
  --vector-size 8 \
  --ckks-mul-depth 4 \
  --openfhe-dir /usr/local/lib/OpenFHE
```

The command internally executes `init → feature → sum / mean / variance →
audit`. `init` is the only stage that creates a CKKS context. It persists
`session/context.bin`, the public key, and the rotation/multiplication
evaluation-key bundles; later
stages load those exact files and do not create another context. The local
benchmark audit also persists a secret key under `session/audit_secret.key`;
that file must stay with the key owner in a real deployment.

Review `feature_comparison.csv` and `aggregation_comparison.csv`; all four
ciphertext artifacts are under `ciphertexts/`:
`payment_diff.ct`, `sum.ct`, `mean.ct`, and `variance.ct`. The output also has
a `max` row, but its HE value is deliberately `NOT_RUN`: exact encrypted max
needs the separate CKKS-to-FHEW comparison/scheme-switching lane. This runner
has one narrow, explicit contract: its group has a **public fixed count of 3**.
It is a valid arithmetic/ciphertext proof, but is not a replacement for the
variable/private group-count finalizer above.

### Standalone encrypted `PAYMENT_DIFF_MAX`

`max` is executed in a dedicated OpenFHE CKKS↔FHEW session, not in the
ordinary HEIR-generated CKKS session. HEIR produces the ordinary arithmetic
lane, while OpenFHE supplies `EvalMaxSchemeSwitching`. The standalone runner
encrypts the two raw parent columns, calculates `PAYMENT_DIFF` after
encryption, and retains only encrypted max. OpenFHE also returns argmax, but
this runner deliberately neither serializes nor decrypts it.

```bash
python3 code/heir/scripts/run_payment_diff_max_openfhe_demo.py \
  --output-dir benchmark_runs/payment_diff_max_switch_01 \
  --overwrite \
  --openfhe-dir /usr/local/lib/OpenFHE
```

Read `max_comparison.csv`. The small three-row review input repeats one genuine
candidate to reach the power-of-two candidate count OpenFHE requires. A
duplicate cannot alter the maximum, unlike a synthetic low sentinel. All real
candidates must still stay inside the public FHEW comparison range recorded as
`execution.max_safe_absolute_input` in `result.json`; an out-of-range value can
wrap modulo the FHEW plaintext space and make max wrong.
See `docs/PAYMENT_DIFF_CIPHERTEXT_FLOW.mmd` for the parent-column, feature,
ciphertext-bundle, and separate-session flow.

### One report-producing installments benchmark

This is the single review command for both source aggregation declarations:

```python
'PAYMENT_PERC': ['max', 'mean', 'sum', 'var']
'PAYMENT_DIFF': ['max', 'mean', 'sum', 'var']
```

```bash
python3 code/heir/scripts/run_installments_aggregation_benchmark.py \
  --output-dir benchmark_runs/installments_aggregation_01 \
  --overwrite \
  --vector-size 8 \
  --ckks-mul-depth 12 \
  --openfhe-dir /usr/local/lib/OpenFHE \
  --allow-partial
```

It writes `REPORT.md`, `result.json`, `kernel_api.json`, and one full log per
lane. `PAYMENT_PERC` measures the encrypted post-encryption ratio alone; its
aggregate cells deliberately say `NOT_RUN` because the chained depth/memory
limit has already been observed. `PAYMENT_DIFF` runs an exact encrypted
subtraction, then saves `payment_diff.ct` and evaluates `sum`, `mean`, and
sample `var` as separately timed process branches which reload the same saved
CKKS session and keys. `max` is documented as deferred because it belongs to
the dedicated CKKS↔FHEW session. `--allow-partial` keeps the report available
even if the constrained server rejects one branch; omit it when a nonzero exit
on any failed lane is preferred.

## Timing and accuracy

Every benchmark records four independent durations:

| Timing | Purpose |
|---|---|
| Python calculation | Headline plaintext baseline; only the source expression |
| Encryption | Recorded operational cost; excluded from headline comparison |
| Encrypted evaluation | Headline HE calculation time |
| Decryption | Accuracy-audit cost; excluded from headline comparison |

The decrypted result is compared to the original Python expression using maximum
absolute and relative error. No benchmark may claim that client-side feature
calculation is encrypted execution.
