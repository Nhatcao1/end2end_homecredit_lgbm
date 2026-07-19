# Home Credit raw data

Place the Home Credit source CSV files in this directory. The CSV files are
intentionally excluded from Git because they are large source datasets.

Expected files:

```text
application_train.csv
application_test.csv
bureau.csv
bureau_balance.csv
previous_application.csv
POS_CASH_balance.csv
installments_payments.csv
credit_card_balance.csv
HomeCredit_columns_description.csv
sample_submission.csv
```

The planned plaintext and HEIR benchmark code should accept this directory as
an input root instead of relying on the original Kaggle `../input` paths.

Generated numeric tensors and other client-prepared artifacts belong in
`data/prepared/`; secret keys and encrypted payloads must remain in their
respective ignored directories.

