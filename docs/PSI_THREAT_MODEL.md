# SecretFlow PSI threat model

## Scope

The PSI lane models two data owners:

- The receiver owns the application population and its TARGET labels.
- The sender owns one Home Credit history table and its derived features.

The current repository data is single-owner, so PSI is optional and exists to
benchmark a future multi-owner deployment. It must not replace the simpler
trusted local join when one client legitimately owns both tables.

## Current security model

| Decision | Current benchmark policy |
|---|---|
| Parties | Two: receiver and sender |
| Adversary | Semi-honest; both follow the configured protocol |
| PSI protocol | SecretFlow PSI v2 `PROTOCOL_RR22` |
| Result recipients | Both parties (`broadcast_result: true`) |
| Revealed result | Ordered intersection and intersection cardinality |
| Sender payload | Remains local; the PSI input contains identifiers only |
| TARGET | Receiver-private; never enters sender exchange or HE tensors |
| Unmatched receiver IDs | Never sent to the sender |
| HE input | Anonymous numeric tensors only, encrypted in a later stage |

The same-host Docker Compose deployment has no TLS configuration. It is a
controlled server benchmark, not a production cross-host deployment. Production
use requires authenticated transport, pinned images, independent cryptographic
review, resource limits, log redaction, and a decision about malicious-party
security.

## Receiver-left-join construction

1. Each party locally extracts unique `SK_ID_CURR` values. History-table
   duplicates are removed before PSI; receiver duplicates are rejected.
2. SecretFlow produces the same ordered intersection for both parties.
3. The bridge randomly assigns a dense `app_index` to every receiver applicant.
4. It creates a sender exchange containing matched identifiers and their random
   slot positions. Other positions contain a blank identifier.
5. The sender places its local feature values into matched slots and represents
   unmatched receiver positions as zero-history slots.
6. Identifiers are removed before numeric staging tensors are encrypted.

This preserves the row count of the original pandas left join. It leaks the
matched identifiers to both parties, as explicitly allowed by the current
benchmark policy. It does not reveal receiver-only identifiers to the sender.

## Not provided

- The CSV bridge cannot attest that SecretFlow produced an output file. It
  records hashes for private audit, while server logs and traces are separate
  execution evidence.
- Standard PSI does not hide the intersection from its result recipients.
- The bridge does not encrypt tensors or serialize CKKS ciphertexts.
- This design does not protect against malicious inputs, protocol deviation,
  traffic analysis, denial of service, or repeated-query inference.
- If neither party may learn the intersection, use circuit PSI or a
  PSI-to-secret-sharing construction instead of this lane.
