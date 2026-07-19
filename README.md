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

Each planned HE workload will produce a Markdown benchmark report containing
its plaintext reference, CKKS result, accuracy comparison, timing breakdown,
artifact sizes, HEIR-generated source proof, and privacy boundary.

