# HE function plan: CPU and GPU

## Goal

Develop one small HE function contract at a time, then implement and benchmark
the same contract on CPU and GPU.

The application-level function must not depend on an OpenFHE or FIDESlib class.
Each backend may use different cryptographic parameters and internal types, but
must accept the same logical input and return the same logical result.

## Hard runtime boundary

- CPU process/image: standard OpenFHE and `openfhe-python` only.
- GPU process/image: FIDESlib and its matching patched OpenFHE only.
- Never link or install both OpenFHE variants in one image or process.
- Compare CPU and GPU through files, serialized messages, and result reports;
  never through shared in-process objects or libraries.

## What we can reuse

- `code/openfhe_direct/api.py`: useful backend-neutral workflow operations and
  the client/evaluator role split.
- `code/openfhe_direct/session.py`: current CPU behavior for encrypted vectors,
  scalars, primitive arithmetic, and reductions. It is a prototype, not the
  shared backend contract.
- `code/openfhe_direct/profiles.py`: reviewed CPU defaults. GPU settings must be
  recorded separately instead of silently reusing these values.
- `code/heir/python_api/official_openfhe_minmax.py`: useful evidence for future
  MIN/MAX work, but it is OpenFHE CPU scheme switching and is outside the first
  GPU scope.

The current `evaluator_view()` separation is logical and same-process. A real
deployment still needs explicit serialization of context, evaluation keys,
inputs, and outputs.

## Development order

Work on only one row at a time:

| Order | Function | Logical result | CPU | GPU |
|---:|---|---|---|---|
| 1 | ADD | element-wise `left + right` | prototype exists | not implemented |
| 2 | SUBTRACT | element-wise `left - right` | prototype exists | not implemented |
| 3 | MULTIPLY | element-wise `left * right` | prototype exists | not implemented |
| 4 | SUM | one global sum across every ciphertext chunk | prototype exists | not implemented |

Do not add MEAN, variance, MIN/MAX, covariance, correlation, or scoring until
these four rows pass on both backends.

## Contract to freeze for each function

1. Plain Python reference function and deterministic input fixture.
2. Input count, slot count, valid count, padding rule, and chunk order.
3. Output shape: vector for primitives, one scalar for global SUM.
4. Absolute and relative error tolerance.
5. Common result fields: backend, function, input count, chunk count,
   repetitions, setup/encrypt/evaluate/decrypt/end-to-end time, errors, status,
   source commit, and image digest.
6. Plaintext and secret key remain client-side; the evaluator receives only
   evaluation material and ciphertext.

## Benchmark sizes

Run in this order:

```text
50,000 -> 100,000 -> 500,000 -> 1,000,000 -> 10,000,000
```

Use one repetition while proving functionality. After a case is stable, use
five measured repetitions. The 10-million case is a stress test, not the first
development gate.

## Per-function workflow

1. Define and test the plaintext reference.
2. Confirm the CPU implementation against it.
3. Freeze the input/output and result-report contract.
4. Implement the same function in the standalone FIDESlib worker.
5. Run CPU and GPU separately with the same deterministic fixture.
6. Compare correctness first, then evaluation and end-to-end timing.
7. Mark the function complete before starting the next row.

## Current status

- CPU primitive and SUM code exists but needs the frozen cross-backend contract.
- CPU benchmark preparation and size commands exist.
- FIDESlib plus patched OpenFHE builds in the standalone GPU image.
- The GPU worker does not yet execute ADD, SUBTRACT, MULTIPLY, or SUM.
- CPU/GPU parameter profiles and serialized transport are not finalized.
- MIN/MAX has no GPU implementation and remains deferred.

## Definition of done

A function is complete only when CPU and GPU independently produce a passing
result for the same fixture and result contract, the two OpenFHE variants stay
in separate images/processes, and no evaluator receives plaintext or a secret
key.
