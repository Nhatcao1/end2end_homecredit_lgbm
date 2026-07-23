# Official Python-facing HEIR operations

The application API is
`code/heir/python_api/official_ckks_aggregates.py`. It calls the official
HEIR package's documented `compile(mlir_str=..., scheme="ckks")` entry point;
it does not invoke a benchmark subprocess or CMake runner.

Use the server's Python 3.12 environment:

```bash
python3 -m venv .venv-heir-py
source .venv-heir-py/bin/activate
python3 -m pip install "heir_py[python,openfhe]==2026.7.1"
```

Run real encrypted fixed-width SUM and MEAN:

```bash
python3 code/heir/examples/heir_py_ckks_sum_mean.py
```

Or run the small timed trial, which writes raw rows, JSON, MLIR sources, and a
Markdown report:

```bash
python3 code/heir/scripts/run_official_heir_py_sum_mean_trial.py \
  --values 160 -100 0 60 250 \
  --width 8 \
  --repetitions 3 \
  --output-dir benchmark_runs/official_heir_py_sum_mean_trial \
  --overwrite
```

Application usage is explicit:

```python
from code.heir.python_api import compile_sum

values = [160.0, -100.0, 0.0]
program = compile_sum(width=8, valid_count=len(values))
program.setup()
input_ct = program.encrypt(values)
sum_ct = program.eval(input_ct)       # still encrypted
sum_value = program.decrypt(sum_ct)   # final client/audit boundary
```

The circuit width and real lane count are public compile-time contracts.
Inputs are zero-padded to the width before encryption.

## Current official capability boundary

| Operation | Official Python route | Status |
|---|---|---|
| SUM | official `compile(mlir_str=..., scheme="ckks")` | implemented |
| MEAN | official compiled SUM × public `1/N` | implemented |
| VAR | official encrypted SUM/SQSUM sample-variance circuit | implemented |
| Multiple encrypted outputs | frontend issue `#1162` | not supported in HEIR 2026.7.1 |
| Ciphertext serialization | backend issue `#1119` | not exposed in HEIR 2026.7.1 |
| Exact MAX/MIN | official OpenFHE Python CKKS↔FHEW switching | separate Python context implemented |

SUM and MEAN are therefore separate official compiled objects. Their `eval`
methods return live ciphertexts, but one object's ciphertext must not be passed
to the other object's context. If one shared context must return SUM, MEAN,
VAR, and MAX together, keep the generated OpenFHE C++ runtime and expose it to
Python through a dedicated binding; the official frontend cannot represent
that interface yet.

## Optional pip-wrapper VAR/MIN/MAX trial

This standalone trial is only for a machine deliberately using the official
OpenFHE Python wrapper. It is **not** the deployment route used by the source-
built OpenFHE server or by `payment_diff_checkpoint_e2e.py`.

For that optional environment, install the wrapper in addition to `heir_py`:

```bash
python3 -m pip install "openfhe==1.5.1.0"
```

Then run a small trial:

```bash
python3 code/heir/scripts/run_official_python_var_minmax_trial.py \
  --values 160 -100 0 60 250 \
  --width 8 \
  --repetitions 1 \
  --ring-dimension 16384 \
  --output-dir benchmark_runs/official_python_var_minmax_trial \
  --overwrite
```

`VAR` and `MIN/MAX` are deliberately reported as separate contexts. The
official HEIR Python runtime and official OpenFHE Python wrapper do not expose
a cross-runtime ciphertext interchange contract.

## Post-PSI grouped PAYMENT_DIFF SUM trial

This trial consumes an existing validated PSI bridge. PSI is not reimplemented
by HEIR: SecretFlow determines the intersection first, then the client replaces
approved `SK_ID_CURR` values with opaque group ordinals and fixed zero-padded
blocks. One official HEIR Python program is reused for every selected group:

```text
Enc(AMT_INSTALMENT), Enc(AMT_PAYMENT)
  -> CT - CT
  -> encrypted fixed-width SUM
  -> final audit decrypt
```

Run five groups:

```bash
python3 code/heir/scripts/run_official_python_post_psi_groupby_trial.py \
  --installments data/home_credit/installments_payments.csv \
  --bridge-dir benchmark_runs/psi/installments_application/rr22_train_test_01 \
  --group-count 5 \
  --bucket-size 128 \
  --output-dir benchmark_runs/official_python_post_psi_groupby_5 \
  --overwrite
```

The raw identifier mapping is written only under `client_private/`.
`he_ready/group_blocks.csv` contains parent columns and padding, never a
plaintext `PAYMENT_DIFF`.

## Full exposed-Python PAYMENT_DIFF E2E

The full Python orchestration adds encrypted MEAN, sample VAR, and exact MAX:

```bash
python3 code/heir/scripts/run_official_python_payment_diff_e2e.py \
  --installments data/home_credit/installments_payments.csv \
  --bridge-dir benchmark_runs/psi/installments_application/rr22_train_test_01 \
  --group-count 2 \
  --bucket-size 128 \
  --output-dir benchmark_runs/official_python_payment_diff_e2e_2groups \
  --overwrite
```

HEIR returns encrypted `[SUM, MEAN, VAR]` as one tensor in one context. MAX is
an explicit second OpenFHE scheme-switching context and re-encrypts only the
two parent columns; it never receives a plaintext `PAYMENT_DIFF`. Parent
amounts are normalized before encryption to keep the squared variance branch
inside the CKKS numerical range.

## Minimal application example

For code review, use the short example instead of the report-producing
benchmark:

```bash
python3 code/heir/examples/payment_diff_post_psi.py \
  --installments data/home_credit/installments_payments.csv \
  --bridge-dir benchmark_runs/psi/installments_application/rr22_train_test_01 \
  --group-count 2 \
  --bucket-size 128 \
  --output-csv benchmark_runs/payment_diff_features.csv \
  --overwrite
```

It performs only the application flow: post-PSI semi-join, opaque grouping,
parent encryption, encrypted PAYMENT_DIFF statistics/MAX, final decryption,
and one output feature CSV. It contains no benchmark timing or report code.

The public cryptographic API is feature-agnostic. Business code composes
column operations explicitly:

```python
statistics = OfficialCkksBinaryColumnStatistics(
    operation="subtract",
    width=128,
    input_scale=scale,
)

ops = OfficialOpenFheColumnOps(
    width=128,
    input_scale=scale,
)
installment_ct = ops.encrypt(installment, padding="duplicate")
payment_ct = ops.encrypt(payment, padding="duplicate")
payment_diff_ct = ops.subtract(installment_ct, payment_ct)
maximum_ct = ops.maximum(payment_diff_ct)
```

The same APIs accept any numeric columns. `OfficialCkksBinaryColumn` exposes
element-wise `add`, `subtract`, and `multiply` HEIR circuits; no kernel knows
the names `PAYMENT_DIFF`, `AMT_PAYMENT`, or `AMT_INSTALMENT`.

The checkpoint E2E example does **not** require the optional pip `openfhe`
wrapper. SUM/MEAN/VAR use the HEIR Python frontend. Its MAX branch is
orchestrated from Python but compiled against the server's source-built
OpenFHE installation via `--openfhe-dir /usr/local/lib/OpenFHE`.

### Conceptual E2E with a checkpoint

This smaller runnable application follows one post-PSI applicant group through
the complete `PAYMENT_DIFF` feature family without benchmark timing/report
machinery:

```bash
python3 code/heir/examples/payment_diff_checkpoint_e2e.py \
  --installments data/home_credit/installments_payments.csv \
  --bridge-dir benchmark_runs/psi/installments_application/rr22_train_test_01 \
  --bucket-size 128 \
  --checkpoint-dir benchmark_runs/payment_diff_checkpoint_example \
  --overwrite
```

Its code path is:

```text
installments CSV
  → post-PSI private semi-join
  → HEIR SUM branch: encrypted parents → subtract → encrypted SUM → checkpoint
  → HEIR MEAN branch: encrypted parents → subtract → encrypted MEAN → checkpoint
  → HEIR VAR branch: encrypted parents → subtract → encrypted VAR → checkpoint
  → OpenFHE branch: encrypted parents → subtract → CKKS↔FHEW encrypted MAX
  → isolated checkpoint reloads
  → final client-only MAX/MEAN/SUM/VAR CSV
```

HEIR 2026.7.1 is more reliable with one encrypted scalar result per compiled
program than with one packed three-result tensor, so SUM, MEAN, and VAR use
separate contexts. No branch receives plaintext `PAYMENT_DIFF`. Exact MAX uses
the same separate OpenFHE scheme-switching route as the benchmark.

OpenFHE evaluation-key maps are process-global. The example therefore reloads
and audits each saved aggregate in a fresh child process, matching a real
restart and avoiding duplicate-key-tag insertion. Use `--resume-checkpoints`
to reuse successfully written SUM/MEAN/VAR checkpoints after a later-stage
failure. There is no benchmark report or timing collection in this example.
