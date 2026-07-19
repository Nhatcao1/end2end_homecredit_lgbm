# End-to-End Home Credit LightGBM and HEIR

This project reconstructs selected operations from the Home Credit
`lightgbm_with_simple_features.py` pipeline and benchmarks privacy-preserving
alternatives using HEIR-generated CKKS/OpenFHE kernels.

## Repository layout

```text
data/home_credit/      local raw Home Credit CSV files (not tracked)
data/prepared/         generated numeric vectors and masks (not tracked)
notebooks/             source pipeline material
benchmark_runs/        generated benchmark reports and artifacts (not tracked)
keys/                  secret/evaluation key material (not tracked)
encrypted_payloads/    ciphertext inputs (not tracked)
```

The HE/HEIR feasibility assessment is documented in
`home_credit_lightgbm_heir_analysis.md`.

Thirteen function-specific benchmark adapters are implemented over five shared
HEIR arithmetic kernels. Prepare their separate Markdown reports with:

```bash
python3 code/heir/scripts/run_function_benchmarks.py --task all --run-name review_v1
```

Each adapter currently records its source-facing plaintext reference, raw
kernel oracle, tensor manifest, preparation timing, artifact sizes, and privacy
boundary. Generated CKKS/OpenFHE execution and decrypted accuracy fields remain
pending and must not be inferred from a `prepared_only` report.

The task registry and current acceptance status are maintained in
`docs/HEIR_BENCHMARK_CRITERIA.md`.
