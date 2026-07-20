# Persistent encrypted feature DAG

## Purpose

This runner tests the HE-compatible Home Credit feature flow end to end while
using only one server process at a time. Every function writes integrity-bound
OpenFHE ciphertexts before the process exits. The next command reloads the
shared CKKS session and continues from disk.

The current scope ends at a combined encrypted feature bundle. It does not
claim encrypted LightGBM/rule execution, unsupported `min`/`max` operations, or
exact encrypted mean/variance finalization. K02 and K03 deliberately persist
encrypted `count`, `sum`, and `sum_squares` sufficient statistics.

## Logical and physical execution

The feature functions are logically independent children of one PSI-derived
applicant layout. The weak-server scheduler nevertheless runs them in source
order:

```text
PSI layout -> shared CKKS session
           -> bureau      -> checkpoint 01
           -> previous    -> checkpoint 02
           -> pos         -> checkpoint 03
           -> installments-> checkpoint 04
           -> credit_card -> checkpoint 05
           -> final zero-copy encrypted bundle index
```

Each checkpoint references all ciphertexts produced so far. It does not copy,
decrypt, or homomorphically combine them.

## Generate HEIR CKKS sources once

The server must have compatible `heir-opt`, `heir-translate`, OpenFHE, CMake,
and a C++17 compiler. Generate all three source-derived kernels at the same
vector size:

```bash
python3 code/heir/scripts/generate_dag_ckks_kernels.py \
  --output-root benchmark_runs/generated_ckks/dag_8192 \
  --vector-size 8192
```

The command runs HEIR's `--mlir-to-ckks` and `--scheme-to-openfhe` pipelines,
then emits installed-OpenFHE C++ and a hash-bound generation manifest. DAG
initialization rejects BGV/BFV output, a missing kernel, or a vector-size
mismatch.

## Initialize one resumable run

Use the saved full-union PSI layout. Keep the smoke scope small on the weak
server; the limits are recorded in the immutable DAG manifest.

```bash
python3 code/heir/scripts/run_dag_stage.py \
  --run-id credit_e2e_smoke_01 \
  --stage init \
  --application benchmark_runs/psi/home_credit_history_union/rr22_full_01/private_exchange/sender_application_layout.csv \
  --psi-manifest benchmark_runs/psi/home_credit_history_union/rr22_full_01/alignment_manifest.json \
  --generated-root benchmark_runs/generated_ckks/dag_8192 \
  --openfhe-dir /path/to/openfhe/lib/OpenFHE \
  --vector-size 8192 \
  --application-row-limit 8 \
  --source-row-limit 500000
```

Initialization uses K03, the deepest active source-derived kernel, to create
one CKKS context/key set. It serializes the context, public key, multiplication
keys, and rotation keys under `session/public/`. The secret key is mode `0600`
under `session/client_private/`; function evaluation runners never load it.

## Run exactly one function per command

```bash
python3 code/heir/scripts/run_dag_stage.py --run-id credit_e2e_smoke_01 --stage bureau
python3 code/heir/scripts/run_dag_stage.py --run-id credit_e2e_smoke_01 --stage previous
python3 code/heir/scripts/run_dag_stage.py --run-id credit_e2e_smoke_01 --stage pos
python3 code/heir/scripts/run_dag_stage.py --run-id credit_e2e_smoke_01 --stage installments
python3 code/heir/scripts/run_dag_stage.py --run-id credit_e2e_smoke_01 --stage credit_card
```

Every command:

1. validates the session and all predecessor hashes;
2. performs the existing client-side function preparation;
3. compiles or reuses cached HEIR-generated K01/K02/K03 executables;
4. encrypts the function inputs with the saved public key;
5. runs the generated function without loading the secret key;
6. serializes each retained output ciphertext;
7. starts a separate continuity probe that reloads and reserializes one result;
8. atomically publishes the stage and appends its zero-copy checkpoint.

An interrupted command leaves an `.inprogress` directory and cannot create a
false `COMPLETED.json`. Inspect it for debugging and rerun the same stage after
resolving the failure. Completed stages can be checked without recomputation:

```bash
python3 code/heir/scripts/run_dag_stage.py \
  --run-id credit_e2e_smoke_01 \
  --stage bureau \
  --resume
```

## Status and finalization

```bash
python3 code/heir/scripts/run_dag_stage.py \
  --run-id credit_e2e_smoke_01 \
  --stage status

python3 code/heir/scripts/run_dag_stage.py \
  --run-id credit_e2e_smoke_01 \
  --stage finalize
```

Finalization refuses to run unless all five completion markers, ciphertext
files, hashes, session IDs, key IDs, and layout IDs validate. Its output is:

```text
benchmark_runs/dag/credit_e2e_smoke_01/final/
├── encrypted_feature_bundle.json
├── benchmark_summary.json
└── benchmark_report.md
```

## Server-only acceptance gate

Unit tests use an internal fixture artifact producer only to test scheduling
and integrity behavior. It is not selectable from the command line. A real run
counts as encrypted completion only when the production runner compiles
HEIR-generated CKKS source, produces non-empty OpenFHE ciphertext files, and
passes the fresh-process continuity probe on the server.

The first server run may reveal a generated-header signature or installed
OpenFHE include difference because both projects evolve. Such a failure must be
fixed against the actual generated header; it must not be bypassed with a mock
or by relabeling plaintext staging output.
