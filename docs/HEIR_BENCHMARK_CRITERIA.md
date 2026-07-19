# HEIR benchmark criteria

This tracker records what each benchmark proves. A task is marked **HEIR
complete** only when generated CKKS/OpenFHE source is compiled and executed;
plaintext preparation or handwritten OpenFHE alone is not sufficient.

## Common acceptance criteria

1. The plaintext reference and its input scope are recorded.
2. Raw applicant identifiers and `TARGET` are excluded from HE tensors.
3. The report identifies client preparation, encrypted work, and trusted
   post-processing separately.
4. HEIR-generated source hashes and the CKKS-only validation result are saved.
5. Decrypted output is compared with the plaintext reference using a declared
   tolerance.
6. Preparation, encryption, evaluation, decryption, and artifact sizes are
   reported independently when available.

## Workload tracker

| ID | Workload | Original operation | CKKS boundary | Status |
|---|---|---|---|---|
| 01 | `pos_count` | `pos.groupby("SK_ID_CURR").size()` | Full encrypted dot-product count after trusted row alignment | Prepare/report implemented; HEIR execution pending generated CKKS source |
| 02 | `credit_card_count` | `cc.groupby("SK_ID_CURR").size()` | Same count kernel as workload 01 | Planned |
| 03 | `installment_payment_diff_sum` | Sum of `AMT_INSTALMENT - AMT_PAYMENT` | CKKS subtraction and sum | Planned |
| 04 | `bureau_active_sums` | Active-credit masked sums | Client mask plus CKKS masked sum | Planned |
| 05 | `bureau_closed_sums` | Closed-credit masked sums | Client mask plus CKKS masked sum | Planned |
| 06 | `previous_approved_sums` | Approved-application masked sums | Client mask plus CKKS masked sum | Planned |
| 07 | `previous_refused_sums` | Refused-application masked sums | Client mask plus CKKS masked sum | Planned |
| 08 | `selected_linear_score` | HE-compatible applicant risk score | CKKS dot product plus bias | Planned |

`min`, `max`, `nunique`, clipped DPD/DBD, encrypted division, and LightGBM tree
inference remain separate approximation/research tasks and are not part of the
initial exact-CKKS acceptance gate.
