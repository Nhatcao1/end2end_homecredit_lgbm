# End-to-End Home Credit LightGBM and HEIR

This project reconstructs selected operations from the Home Credit
`lightgbm_with_simple_features.py` pipeline and benchmarks privacy-preserving
alternatives using HEIR-generated CKKS/OpenFHE kernels.

## Repository layout

```text
data/home_credit/      local raw Home Credit CSV files (not tracked)
data/prepared/         generated numeric vectors and masks (not tracked)
data/psi/              party-private PSI inputs/outputs/traces (not tracked)
notebooks/             source pipeline material
benchmark_runs/        generated benchmark reports and artifacts (not tracked)
keys/                  secret/evaluation key material (not tracked)
encrypted_payloads/    ciphertext inputs (not tracked)
deploy/secretflow_psi/ same-host SecretFlow PSI benchmark deployment
code/bridge/            validated PSI-to-HEIR layout bridge
```

The HE/HEIR feasibility assessment is documented in
`home_credit_lightgbm_heir_analysis.md`.

Five complete function benchmarks are implemented over shared HEIR arithmetic
kernels. Run one original source function at a time with:

```bash
python3 code/heir/scripts/run_function_benchmarks.py \
  --function bureau \
  --application-row-limit 8 \
  --source-row-limit 500000 \
  --run-name bureau_smoke
```

Available functions are `bureau`, `previous`, `pos`, `installments`, and
`credit_card`. Each produces one combined Markdown report, consolidated oracle,
tensor manifest, and bundle-ready schema. Generated CKKS/OpenFHE execution and
ciphertext serialization remain pending and must not be inferred from a
`prepared_only` / `plaintext_staging_only` report.

The function registry and current acceptance status are maintained in
`docs/HEIR_BENCHMARK_CRITERIA.md`.

## Optional private join

When application and history tables belong to different parties, the optional
SecretFlow adapter extracts unique identifier sets, runs PSI outside HEIR, and
converts the aligned intersection into dense anonymous HEIR slots. Local
single-owner runs should continue to join identifiers directly and skip PSI.

The server workflow is documented in `deploy/secretflow_psi/README.md`. Its
security boundary and current acceptance status are recorded in
`docs/PSI_THREAT_MODEL.md` and `docs/PSI_BENCHMARK_CRITERIA.md`.
For the exact `installments_payments` receiver-left join, use
`docs/INSTALLMENTS_PSI_RUNBOOK.md`.
For the small complete post-PSI `PAYMENT_DIFF_{MAX,MEAN,SUM,VAR}` proof, use
`docs/PAYMENT_DIFF_POST_PSI_E2E.md`.
The definitive data, ciphertext, key, timing, and audit flow is in
`docs/POST_PSI_PAYMENT_DIFF_E2E_FLOW.md`.
