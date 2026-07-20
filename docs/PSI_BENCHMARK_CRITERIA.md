# SecretFlow PSI benchmark criteria

## Decision

Use SecretFlow PSI as an optional external identifier-alignment stage for
multi-owner experiments. Do not treat PSI as an HEIR kernel, and do not run it
for the ordinary single-owner Home Credit pipeline.

## Acceptance criteria

| ID | Criterion | Status |
|---|---|---|
| PSI01 | Extract receiver identifiers and reject blank/duplicate keys | Implemented and unit tested |
| PSI02 | Deduplicate repeated history-table identifiers | Implemented and unit tested |
| PSI02A | Union and audit identifiers across all sender-owned feature tables | Implemented and unit tested |
| PSI03 | Run two SecretFlow PSI v2 roles concurrently | Docker Compose/config implemented; server execution pending |
| PSI04 | Require identical ordered receiver/sender outputs | Implemented and unit tested |
| PSI05 | Reject PSI results absent from receiver applications | Implemented and unit tested |
| PSI06 | Preserve receiver-left-join row count with blank sender slots | Implemented and unit tested |
| PSI07 | Exclude TARGET and receiver-only IDs from sender exchange | Implemented and unit tested |
| PSI08 | Exclude raw IDs from public HE staging tensors | Implemented and unit tested |
| PSI09 | Feed the dense sender layout into a complete HEIR function benchmark | Implemented and unit tested |
| PSI10 | Generate a PSI/bridge Markdown report and manifests | Implemented and unit tested |
| PSI11 | Record SecretFlow logs/traces and wall time | Runner implemented; server execution pending |
| PSI11A | Bind the recorded server summary to exact PSI output hashes in the Markdown report | Implemented and unit tested |
| PSI12 | Encrypt aligned tensors and retain ciphertext across functions | Persistent DAG code implemented; server CKKS execution pending |
| PSI13 | Production TLS and malicious-party review | Out of current benchmark scope |

## Benchmark artifacts

```text
data/psi/                         private, ignored
├── receiver/psi_input.csv
├── receiver/psi_output.csv
├── receiver/receiver.trace
├── sender/psi_input.csv
├── sender/psi_output.csv
└── sender/sender.trace

benchmark_runs/psi/<source>/<run-name>/   private runtime output, ignored
├── alignment_manifest.json
├── psi_bridge_report.md
├── client_private/
│   ├── receiver_application_layout.csv
│   └── psi_output_audit.json
├── private_exchange/
│   ├── sender_application_layout.csv
│   └── sender_match_mapping.csv
└── heir_staging/
    └── sender_presence_mask.csv
```

`sender_presence_mask.csv` is plaintext staging, not a ciphertext. It must not
leave the data-owner boundary before HE encryption.
