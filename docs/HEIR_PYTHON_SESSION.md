# HEIR-Python SUM and MEAN session

`code/heir_python/session.py` provides a small application-facing API:

```python
from code.heir_python import HeirCkksSession

session = HeirCkksSession(width=8, valid_count=3)
session.setup()

values_ct = session.encrypt([160.0, -100.0, 0.0])
sum_ct = session.sum(values_ct)
mean_ct = session.mean(values_ct)

sum_value = session.decrypt(sum_ct)
mean_value = session.decrypt(mean_ct)
```

## Public methods

| Method | Input | Output |
|---|---|---|
| `setup()` | Public width/count configuration | Compiled HEIR SUM and MEAN programs with contexts and keys |
| `encrypt(values)` | Exactly `valid_count` finite numbers | `EncryptedColumn` |
| `sum(column)` | Same-session `EncryptedColumn` | Encrypted scalar |
| `mean(column)` | Same-session `EncryptedColumn` | Encrypted scalar |
| `decrypt(scalar)` | Same-session encrypted scalar | Final audit float |

## Current HEIR-Python constraint

SUM and MEAN are separate HEIR-compiled programs. Each program owns a separate
OpenFHE context and its ciphertexts cannot be passed to the other program.
Consequently `encrypt()` creates two encrypted branches for one logical input.
This wrapper does not pretend that those branches are one physical ciphertext.

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
