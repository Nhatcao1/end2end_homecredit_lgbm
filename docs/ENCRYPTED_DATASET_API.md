# EncryptedDataset checkpoint API

`EncryptedDataset` is the small public API for persisting named encrypted
columns. It is not an encrypted Pandas implementation.

```python
from pathlib import Path
from code.heir.python_api import EncryptedDataset

dataset = EncryptedDataset.encrypt(
    {
        "AMT_INSTALMENT": [800.0, 500.0, 1000.0],
        "AMT_PAYMENT": [640.0, 600.0, 1000.0],
    },
    operation="subtract",
    width=8,
    input_scale=2048.0,
)
dataset.save(
    Path("checkpoint"),
    include_audit_key=True,
)
```

Load and evaluate in a fresh process:

```python
dataset = EncryptedDataset.load(
    Path("checkpoint"),
    for_audit=True,
)
payment_diff_ct = dataset.evaluate(
    "AMT_INSTALMENT",
    "AMT_PAYMENT",
)
payment_diff = dataset.decrypt_result(payment_diff_ct)
```

For an evaluator deployment, omit the secret key:

```python
dataset.save(Path("evaluator_checkpoint"))
dataset = EncryptedDataset.load(Path("evaluator_checkpoint"))
payment_diff_ct = dataset.evaluate(
    "AMT_INSTALMENT",
    "AMT_PAYMENT",
)
```

The evaluator can calculate and retain `payment_diff_ct`, but
`decrypt_result()` fails because no audit secret is present.

Version 1 deliberately supports exactly two named columns associated with one
compiled HEIR operation. The manifest binds ciphertexts to their CKKS context,
scale, width, circuit hash, column order, and library versions. Ciphertexts
from unrelated manifests must not be mixed.

Run the fresh-process example:

```bash
python3 code/heir/examples/encrypted_dataset_save_load.py \
  --checkpoint-dir benchmark_runs/encrypted_dataset_trial \
  --overwrite
```
