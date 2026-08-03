# HEIR-Python aggregate session and backend policy

`code/heir_python/session.py` provides a small application-facing API:

```python
from code.heir_python import HeirCkksSession

session = HeirCkksSession(width=8, valid_count=3)
session.setup()

values_ct = session.encrypt([160.0, -100.0, 0.0])
sum_ct = session.sum(values_ct)
mean_ct = session.mean(values_ct)
variance_ct = session.variance(values_ct)

sum_value = session.decrypt(sum_ct)
mean_value = session.decrypt(mean_ct)
variance_value = session.decrypt(variance_ct)
```

## Public methods

| Method | Input | Output |
|---|---|---|
| `setup()` | Public width/count configuration | Compiled HEIR SUM, MEAN, and VARIANCE programs with contexts and keys |
| `encrypt(values)` | Exactly `valid_count` finite numbers | `EncryptedColumn` |
| `sum(column)` | Same-session `EncryptedColumn` | Encrypted scalar |
| `mean(column)` | Same-session `EncryptedColumn` | Encrypted scalar |
| `variance(column)` | Same-session `EncryptedColumn` | Encrypted sample variance |
| `decrypt(scalar)` | Same-session encrypted scalar | Final audit float |

## Current HEIR-Python constraint

SUM, MEAN, and VARIANCE are separate HEIR-compiled programs. Each owns a
separate OpenFHE context and its ciphertexts cannot be passed to another
program. Consequently `encrypt()` creates three encrypted branches for one
logical input. The wrapper does not pretend they are one physical ciphertext.

HEIR-Python currently does not expose the OpenFHE CKKS-to-FHEW scheme-switching
path needed for exact encrypted comparisons, MIN, or MAX. Those operations use
`code.openfhe_direct.OpenFHECreditSession` instead. Backend selection must occur
before encryption: a ciphertext created inside an HEIR-owned context cannot be
handed to an independently created OpenFHE-Python context.

## Backend routing policy

| Workload | Preferred route | Reason |
|---|---|---|
| CKKS arithmetic and future smooth polynomial workloads | HEIR-Python | Compiler-managed CKKS program |
| SUM, fixed-count MEAN, sample VARIANCE | HEIR-Python | Implemented as compiled CKKS circuits |
| Comparison, MIN, MAX | OpenFHE-Python | Requires CKKS-to-FHEW scheme switching not exposed by HEIR-Python |
| Unsupported HEIR operation | OpenFHE-Python only after explicit review | Never silently decrypt or fake an HE result |

This is a preference, not a claim that HEIR and direct OpenFHE ciphertexts are
interchangeable. A combined workload may require separately encrypted branches
until HEIR-Python exposes a shared-context/multi-output route.

## Run

```bash
cd /root/end2end_homecredit_lgbm
source .venv-heir/bin/activate

python3 -m code.heir_python.example_sum_mean \
  --values 160 -100 0 \
  --width 8 \
  --debug
```

The implementation uses the official `heir.compile(..., scheme="ckks")`
route through the existing aggregate compiler. It does not import OpenFHE,
invoke CMake, or run generated C++ directly.
