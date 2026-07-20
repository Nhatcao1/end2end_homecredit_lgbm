# SecretFlow PSI deployment adapter

This directory contains a same-host, two-container SecretFlow PSI v2 benchmark
configuration. SecretFlow itself is not vendored into this repository.

The configuration runs `PROTOCOL_RR22`, broadcasts the aligned intersection to
both parties, and writes ordered output files under `data/psi/`. It is intended
for controlled benchmarking on the remote server. It has no TLS configuration
and must not be treated as a production cross-host deployment.

Runtime inputs, outputs, traces, and `.env` are ignored by Git. Commit only the
example deployment configuration. Resolve and pin the image digest on the
server before recording final performance numbers.

## Server smoke run

From the repository root, prepare a receiver application set and the union of
all five sender history-table applicant sets. Repeat `--sender-source` once per
table. The adapter deduplicates applicants both within and across the tables.
`bureau_balance.csv` is not listed because it has `SK_ID_BUREAU`, not
`SK_ID_CURR`; its applicant association comes through `bureau.csv`.

```bash
mkdir -p data/psi/receiver data/psi/sender

python3 code/private_join/scripts/prepare_psi_inputs.py \
  --receiver-source data/home_credit/application_train.csv \
  --sender-source data/home_credit/bureau.csv \
  --sender-source data/home_credit/previous_application.csv \
  --sender-source data/home_credit/POS_CASH_balance.csv \
  --sender-source data/home_credit/installments_payments.csv \
  --sender-source data/home_credit/credit_card_balance.csv
```

Pull the official image, inspect its immutable digest, and copy the example
environment file. Replace the image value in `.env` with the displayed
`RepoDigest` before recording final benchmarks.

```bash
docker pull secretflow/psi-anolis8:latest
docker image inspect \
  --format '{{index .RepoDigests 0}}' \
  secretflow/psi-anolis8:latest

cp deploy/secretflow_psi/.env.example deploy/secretflow_psi/.env
```

Run both roles concurrently. The Python wrapper streams Compose output and
records elapsed time, configured image, log hash, trace hashes, and validated
output hashes:

```bash
python3 code/private_join/scripts/run_secretflow_psi.py
```

You can independently repeat the output-contract validation without rerunning
the containers:

```bash
python3 code/private_join/scripts/validate_psi_outputs.py
```

Create the dense receiver-left-join layout and Markdown report:

```bash
python3 code/bridge/psi_to_heir.py \
  --receiver-source data/home_credit/application_train.csv \
  --receiver-psi-output data/psi/receiver/psi_output.csv \
  --sender-psi-output data/psi/sender/psi_output.csv \
  --sender-name home_credit_history_union \
  --output-dir benchmark_runs/psi/home_credit_history_union/rr22_smoke
```

Finally, confirm that a complete function benchmark consumes the sender layout.
Keep the application limit small for this smoke test:

```bash
python3 code/heir/scripts/run_function_benchmarks.py \
  --function bureau \
  --application benchmark_runs/psi/home_credit_history_union/rr22_smoke/private_exchange/sender_application_layout.csv \
  --application-row-limit 8 \
  --source-row-limit 500000 \
  --run-name bureau_after_psi_smoke
```

The PSI bridge report is written to
`benchmark_runs/psi/home_credit_history_union/rr22_smoke/psi_bridge_report.md`. The function report
is written under
`benchmark_runs/functions/bureau_and_balance/bureau_after_psi_smoke/`.

Use a new output directory and run name for every repeat; the bridge and HEIR
runners intentionally refuse to overwrite previous results. The PSI wrapper
also refuses to overwrite existing party output files; archive or remove the
previous ignored runtime directory before starting another PSI execution.
