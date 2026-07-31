# Simple OpenFHE-service credit-rating flow

This is the preferred small application path when the K3s OpenFHE gateway is
available. It does not import HEIR, generate MLIR, invoke CMake, or install
OpenFHE on the calling machine.

```text
prepared installment group CSV
  → HEClient
  → K3s he-gateway
  → OpenFHE Python/C++
  → final explicit decrypt
  → JSON
```

The example uses the two Home Credit parent-column equivalents:

```python
AMT_INSTALMENT
AMT_PAYMENT
```

It calculates encrypted addition, subtraction (`PAYMENT_DIFF`),
multiplication, public-scalar/public-vector arithmetic, square, sum, mean,
variance components, covariance components, correlation components, weighted
sum, and a risk-score demonstration. One `HEClient` context is reused for all
requested prepared groups.

MIN/MAX is deliberately absent because the current gateway does not implement
comparison or vector extrema.

Run:

```bash
python3 code/openfhe_service/credit_rating_demo.py \
  --gateway-url http://127.0.0.1:18082 \
  --prepared-group data/prepared/examples/payment_diff_demo_group.csv \
                   data/prepared/examples/payment_diff_demo_group_002.csv \
                   data/prepared/examples/payment_diff_demo_group_003.csv \
  --output benchmark_runs/openfhe_service_credit_results.json
```

The calling environment needs the `he_client` package but does not need HEIR
or OpenFHE. The gateway Pod remains trusted because it owns the session,
plaintext request input, and secret key.
