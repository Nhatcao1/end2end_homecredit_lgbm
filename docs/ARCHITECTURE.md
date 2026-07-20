# End-to-end privacy architecture

## Scope

This architecture assesses the HE-compatible subset of the Home Credit feature
pipeline. PSI aligns applicant identifiers once. Five feature functions then
run sequentially under one CKKS session and persist encrypted sufficient
statistics. The current encrypted pipeline ends at the combined feature-bundle
index; LightGBM, rules, exact `min`/`max`, and encrypted mean/variance
finalization are outside the implemented path.

## Overall trust and data flow

```mermaid
flowchart LR
    subgraph Receiver[Receiver / application owner]
        RA[application_train.csv]
        RK[SK_ID_CURR set]
        RL[Private receiver layout]
        SK[Secret key<br/>audit use only]
        RA --> RK
    end

    subgraph Sender[Sender / history owner]
        ST[Five history tables]
        SU[Union of SK_ID_CURR]
        FP[Client feature preparation<br/>clean, group, mask, pad]
        ST --> SU
        ST --> FP
    end

    subgraph PSI[SecretFlow PSI boundary]
        RR[RR22 private intersection]
    end

    RK --> RR
    SU --> RR
    RR --> AL[Anonymous dense app_index layout]
    AL --> RL
    AL --> FP

    subgraph Session[Shared CKKS session]
        CC[Crypto context]
        PK[Public key]
        EK[Evaluation and rotation keys]
    end

    subgraph Server[HEIR / OpenFHE evaluation server]
        ENC[Encrypt prepared tensors]
        HE[HEIR-generated K01 / K02 / K03]
        CT[Serialized ciphertext outputs]
        CP[Integrity-bound checkpoints]
        ENC --> HE --> CT --> CP
    end

    FP --> ENC
    AL -. layout hash .-> CP
    CC --> ENC
    PK --> ENC
    CC --> HE
    EK --> HE
    CP --> FINAL[Final encrypted feature-bundle index]

    subgraph Audit[Separate audit path]
        COPY[Validation copy]
        DEC[Decrypt]
        REF[Plaintext oracle comparison]
        COPY --> DEC --> REF
    end

    CT -. optional copy .-> COPY
    SK --> DEC
```

The normal evaluation server never receives the secret key. Plaintext
references and audit decryption results are not valid inputs to a later
encrypted stage.

## Serial encrypted DAG

The feature branches are logically independent, but the weak-server scheduler
runs one process at a time. Every process exits only after its ciphertexts and
completion record are on disk.

```mermaid
flowchart TD
    P[PSI alignment manifest] --> S[Initialize shared CKKS session]
    S --> C0[Checkpoint 00<br/>layout anchor]

    C0 --> B[Bureau<br/>K01 + K02]
    B --> C1[Checkpoint 01]

    C1 --> V[Previous applications<br/>K01 + K02]
    V --> C2[Checkpoint 02]

    C2 --> POS[POS cash<br/>K01 + K02]
    POS --> C3[Checkpoint 03]

    C3 --> I[Installments<br/>K01 + K02 + K03]
    I --> C4[Checkpoint 04]

    C4 --> CC[Credit card<br/>K01 + K02]
    CC --> C5[Checkpoint 05]

    C5 --> F[Final encrypted bundle index]

    B -. process exits .-> C1
    V -. process exits .-> C2
    POS -. process exits .-> C3
    I -. process exits .-> C4
    CC -. process exits .-> C5
```

Although the diagram is linear physically, a function stage reads only the
shared session, applicant layout, its own source table, and its prepared
tensors. It does not load the previous functions' ciphertext values. The new
checkpoint appends file references to the previous checkpoint.

## One function stage

```mermaid
sequenceDiagram
    participant CLI as run_dag_stage.py
    participant Prep as Client preparation
    participant HEIR as Generated HEIR/OpenFHE runner
    participant Disk as Encrypted artifact store
    participant Probe as Fresh continuity process

    CLI->>CLI: Validate session and predecessor hashes
    CLI->>Prep: Read one source table
    Prep-->>CLI: Fixed-shape numeric tensors and masks
    CLI->>HEIR: Context, public/evaluation keys, tensors
    Note over HEIR: Secret key is not loaded
    HEIR->>HEIR: Encrypt and evaluate K01/K02/K03
    HEIR->>Disk: Serialize result ciphertexts
    CLI->>Probe: Reload one ciphertext in a new process
    Probe->>Disk: Reserialize continuity copy
    Probe-->>CLI: Level, scale and success evidence
    CLI->>Disk: Write manifest and benchmark report
    CLI->>Disk: Write COMPLETED.json last
    CLI->>Disk: Atomically publish checkpoint
```

## Persistent artifact layout

```text
benchmark_runs/dag/<run-id>/
├── dag_manifest.json
├── session/
│   ├── public/
│   │   ├── crypto_context.bin
│   │   ├── public_key.bin
│   │   ├── evaluation_mult_keys.bin
│   │   └── evaluation_rotation_keys.bin
│   ├── client_private/
│   │   └── secret_key.bin
│   ├── session_manifest.json
│   └── COMPLETED.json
├── build_cache/
│   ├── session_initializer/
│   ├── evaluate_k01/
│   ├── evaluate_k02/
│   ├── evaluate_k03/
│   └── continuity_probe/
├── stages/
│   ├── 01_bureau/
│   ├── 02_previous/
│   ├── 03_pos/
│   ├── 04_installments/
│   └── 05_credit_card/
├── checkpoints/
│   ├── 00_layout/
│   ├── 01_bureau/
│   ├── 02_previous/
│   ├── 03_pos/
│   ├── 04_installments/
│   └── 05_credit_card/
└── final/
    ├── encrypted_feature_bundle.json
    ├── benchmark_summary.json
    └── benchmark_report.md
```

Each function stage contains:

```text
<stage>/
├── preparation/                 plaintext client/audit boundary
├── ciphertexts/                 downstream encrypted outputs
├── continuity_probe/reloaded.ct
├── feature_bundle_manifest.json
├── benchmark_summary.json
├── benchmark_report.md
└── COMPLETED.json               written last
```

## Artifact compatibility contract

```mermaid
flowchart LR
    A[Stage ciphertext] --> H[SHA256 and byte size]
    M[Stage manifest] --> IDs[session_id<br/>context_id<br/>key_set_id<br/>layout hash]
    H --> DONE[COMPLETED.json]
    IDs --> DONE
    DONE --> NEXT{Next stage validation}
    NEXT -->|all match| ACCEPT[Accept predecessor]
    NEXT -->|missing or changed| REJECT[Fail closed]
```

A stage is downstream-ready only when:

- `bundle_status` is `encrypted_complete`;
- `ciphertext_files` is non-empty;
- every ciphertext exists and matches its recorded hash;
- session, context, key-set, and applicant-layout identifiers match;
- the fresh-process continuity probe succeeded;
- `COMPLETED.json` validates.

`prepared_only` and `plaintext_staging_only` never satisfy this contract.

## Arithmetic ownership

| Operation | Owner | Encrypted output |
|---|---|---|
| PSI identifier comparison | SecretFlow PSI | No; anonymous layout |
| Missing-value policy, strings, categories, grouping and padding | Data owner | Prepared tensors |
| K01 masked dot product/count | HEIR CKKS | Encrypted count/sum |
| K02 moments | HEIR CKKS | Encrypted count, sum, sum-of-squares |
| K03 difference moments | HEIR CKKS | Encrypted difference statistics |
| Join after common `app_index` alignment | Bundle index | Existing ciphertext references |
| Accuracy decryption | Separate client audit | Plaintext validation only |
| LightGBM/rules and unsupported comparisons | Outside current DAG | None |

## Related documents

- [ENCRYPTED_DAG.md](ENCRYPTED_DAG.md) — server commands and operational procedure.
- [HEIR_BENCHMARK_CRITERIA.md](HEIR_BENCHMARK_CRITERIA.md) — feature/kernel acceptance criteria.
- [PSI_BENCHMARK_CRITERIA.md](PSI_BENCHMARK_CRITERIA.md) — PSI boundary and status.
- [PSI_THREAT_MODEL.md](PSI_THREAT_MODEL.md) — leakage and trust assumptions.
