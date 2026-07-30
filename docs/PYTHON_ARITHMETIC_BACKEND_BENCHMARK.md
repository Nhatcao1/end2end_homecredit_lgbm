# Python arithmetic backend comparison

This benchmark runs primitive encrypted arithmetic with either:

- HEIR Python, the canonical backend;
- OpenFHE Python, an optional comparison backend;
- both backends on identical deterministic inputs.

Operations:

```text
CT + CT
CT - CT
CT × CT
```

Metrics:

| Category | Recorded values |
|---|---|
| One-time cost | HEIR compilation, context/key setup |
| Latency | Python, encryption, HE evaluation, decryption, online total |
| Throughput | Evaluation values/s and online values/s |
| Accuracy | MAE, maximum absolute error, mean/max relative error |

`Online` means encryption + encrypted evaluation + final audit decryption.
Compilation/setup is reported separately and is not silently amortized.

The logical inputs, packing width, public normalization scale, and requested
ring dimension are matched. The report records the actual runtime ring
dimension when the backend exposes it. HEIR still chooses its generated
modulus chain, so the result is an implementation-path comparison, not a
claim that the two backends have byte-identical cryptographic parameters.

## Run both backends

```bash
source .venv-heir-python/bin/activate

python3 code/heir/scripts/run_python_arithmetic_backend_benchmark.py \
  --backend both \
  --value-count 1000 50000 \
  --slot-count 8192 \
  --ring-dimension 16384 \
  --decimal-places 3 \
  --repetitions 5 \
  --output-dir benchmark_runs/python_arithmetic_both \
  --overwrite
```

For a first smoke test:

```bash
python3 code/heir/scripts/run_python_arithmetic_backend_benchmark.py \
  --backend both \
  --value-count 1000 \
  --slot-count 1024 \
  --ring-dimension 16384 \
  --repetitions 1 \
  --output-dir benchmark_runs/python_arithmetic_smoke \
  --overwrite
```

Run only one backend by changing `--backend` to `heir` or
`openfhe-python`.

Artifacts:

```text
results.csv
summary.json
REPORT.md
```

The report includes direct OpenFHE/HEIR evaluation and online latency ratios
when both backends run.

OpenFHE Python is an explicit comparison override for these primitive
operations. It does not change the canonical policy: production-oriented
benchmark routes continue to prefer HEIR whenever HEIR supports the
calculation.
