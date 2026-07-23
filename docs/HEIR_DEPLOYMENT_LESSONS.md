# HEIR / OpenFHE deployment lessons

This is a practical preflight for the Home Credit encrypted pipeline. It is
based on failures found while moving from individual feature proofs to chained
ciphertext stages. It is not a claim that every stage below has passed on every
HEIR/OpenFHE version.

## What failed and what to retain

| Symptom | Cause | Deployment rule |
|---|---|---|
| `heir-opt: No such file or directory` | HEIR virtual environment was not active or its binaries were not on `PATH`. | Activate `.venv-heir` and verify both `heir-opt` and `heir-translate` before generation. |
| `ModuleNotFoundError: No module named 'openfhe'` in the Python MAX path | `heir_py[openfhe]` supplies HEIR's generated native backend, not the separately importable official OpenFHE Python wrapper used for CKKS↔FHEW MIN/MAX. | Install `openfhe==1.5.1.0` into the same active virtual environment and verify it with that environment's Python. This is a missing package, not a ciphertext/context-version mismatch. |
| MLIR parse errors such as float literal or `tensor.extract` index errors | Generated MLIR did not meet MLIR syntax requirements. | Lower each source independently before attempting C++ generation; use SSA `index` values for `tensor.extract`. |
| `no such option mul-depth` | Server has an older HEIR pipeline with only `entry-function` exposed through `scheme-to-openfhe`. | Check `heir-opt --help` on the target server. For the current server, the review runners patch the translated `SetMultiplicativeDepth(...)` line and record it in the run manifest. Do not assume newer pipeline flags exist. |
| CKKS `SetLevel` / insufficient depth | A later stage needed levels that the context created for an earlier stage did not reserve. | Determine the deepest planned path before key generation. Create one context from the deepest stage and use it throughout that connected ciphertext DAG. |
| `Decode(): approximation error is too high` | The reciprocal-based `PAYMENT_PERC` circuit had too little CKKS precision/depth. | Validate the ratio alone with a public denominator-range contract and explicit depth before adding aggregates. Sum does not fix ratio noise. |
| Row outputs became the same as their aggregate | A generated sum consumed/mutated its ciphertext input in place. | Treat an input to generated HEIR code as **consumed** unless proven otherwise. Serialize/audit it before the consumer; materialize a separate ciphertext branch when more than one downstream stage needs it. |
| `std::get<0>(momentsStruct)` compile error | HEIR emits custom multiple-return structs, not `std::tuple`. | Inspect the generated header. Access multi-results as `.arg0`, `.arg1`, etc. Add an ABI compile test for every new multi-output kernel. |
| `EvalBootstrapSetup operation has not been enabled` | HEIR inserted bootstrap in the deep finalizer, but the shared context was created by a shallow kernel without FHE/bootstrap setup. | If bootstrap is present, create/configure the shared context from the bootstrap/deepest stage first. Treat it as a separate expensive deployment lane. |
| Generated runner exits with `-9` and no output | The OS/container sent `SIGKILL`, normally because bootstrap setup/key generation exhausted the server memory limit. | Do not retry the same bootstrap run blindly. Check memory/OOM logs, then move the bootstrap experiment to a larger machine or keep encrypted sufficient statistics (`count`, `sum`, `sum_squares`) as the terminal server-side output. |
| CKKS serialization-registration errors | Required OpenFHE serialization headers/types were not registered in the runner. | Include the CKKS serialization headers in runners that write ciphertext artifacts and verify serialization before full execution. |
| `InsertEvalMultKey(): ... key vector for the given keyTag` while reloading a checkpoint | OpenFHE keeps evaluation keys in process-global maps. The process that created a key still has it registered, so deserializing the same key tag again is rejected. | Model reload as a real restart: restore and audit each checkpoint in a fresh process. Do not repeatedly deserialize saved evaluation keys into their creator process. |
| `EvalFastRotationExt(): EvalKey for index [...] is not found` during CKKS↔FHEW MIN/MAX | `SchSwchParams.SetComputeArgmin(false)` suppresses the comparison-tree rotation keys, although `EvalMaxSchemeSwitching` still needs them internally. | Set `ComputeArgmin(true)` before `EvalSchemeSwitchingKeyGen`; keep only result `[0]` (encrypted MAX) and discard the returned encrypted argmax. This stays in the same context and does not decrypt/re-encrypt the feature. |

## Full deployment preflight

Run these checks before launching a full dataset DAG.

1. Verify the server toolchain and record its version/capabilities.

   ```bash
   source .venv-heir/bin/activate
   command -v heir-opt
   command -v heir-translate
   heir-opt --help | rg 'scheme-to-openfhe|mul-depth|bootstrap'
   test -f /usr/local/lib/OpenFHE/OpenFHEConfig.cmake
   ```

2. Generate every kernel individually and retain its MLIR, lowered MLIR,
   generated header, generated C++, hashes, and requested/inferred CKKS depth.
   Stop on any lowering failure. Do not discover syntax errors during a full run.

3. Build one small C++ ABI harness containing the exact set of generated
   kernels planned for a DAG stage. Verify:

   - shared context type and key configuration;
   - generated multi-return field names;
   - serialization of every ciphertext artifact;
   - ownership/consumption rules for each kernel input.

4. Define client-side preparation contracts before encryption:

   - missing/sentinel handling and encrypted validity masks;
   - safe denominator substitution for divisions;
   - public value ranges and normalization scales;
   - public valid-count range when encrypted mean/sample variance is enabled.

   Client preparation may make data safe to encrypt; it must not calculate the
   protected feature itself.

5. Build the DAG from deep to shallow. The context owner is the stage with the
   largest required depth, or any stage containing bootstrap. All upstream and
   downstream ciphertext operations in that connected execution must use that
   one context and matching keys.

6. Test in this order:

   ```text
   exact feature → exact sum
   ratio feature alone → accuracy audit
   ratio feature → sum
   feature → count/sum/sum_squares
   moments → mean/sample variance
   full grouped / joined DAG
   ```

7. Keep ciphertext artifacts and a JSON manifest after each stage. For each
   artifact record producer kernel, context identifier, input artifact hashes,
   requested depth, whether the input was consumed, and the next allowed
   consumers. This makes the encrypted pipeline auditable like an Airflow DAG.

## Bootstrap decision

Do **not** use bootstrap merely because an operation is inconvenient. First try
range conditioning, an appropriate CKKS depth/modulus chain, and a shallower
approximation. Bootstrap is justified only when an accuracy-validated long path
still exhausts its modulus levels and its additional runtime/key cost is
acceptable. It should have its own benchmark and deployment configuration.

## Current status

- Exact `PAYMENT_DIFF → sum` has been exercised as a ciphertext chain.
- `run_payment_diff_fixed_count_aggregates.py` is the next small, executable
  arithmetic proof: `PAYMENT_DIFF → sum / mean / sample variance` from one
  encrypted source artifact. It deliberately uses a public fixed count of
  three review lanes, so it does **not** represent variable-size grouped
  aggregation. Its `max` row is intentionally `NOT_RUN` until the separate
  CKKS-to-FHEW comparison benchmark exists.
- `PAYMENT_PERC` requires its own depth/range accuracy proof before use in a
  larger DAG.
- `PAYMENT_DIFF → moments → mean/sample variance` is an explicit bootstrap
  experiment; it must be evaluated separately from the ordinary non-bootstrap
  sum lane.
