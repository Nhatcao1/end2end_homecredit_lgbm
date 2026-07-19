"""Installment count, prepared moments, and encrypted difference definitions."""

from code.heir.workloads.grouped import FeatureSpec, TaskSpec


TASKS = (
    TaskSpec(
        task_id="I01",
        slug="installment_count",
        function_name="installments_payments",
        title="Installment row count",
        input_file="installments_payments.csv",
        source_lines="199-229",
        kind="count",
        kernel_ids=("K01",),
        client_preparation=("align rows to anonymous applicant indices",),
        excluded_outputs=("nunique",),
    ),
    TaskSpec(
        task_id="I02",
        slug="prepared_aggregates",
        function_name="installments_payments",
        title="Installment client-prepared supported aggregates",
        input_file="installments_payments.csv",
        source_lines="199-229",
        kind="moments",
        kernel_ids=("K01", "K02"),
        features=(
            FeatureSpec(
                "DPD",
                ("mean", "sum"),
                transform="positive_difference",
                source_columns=("DAYS_ENTRY_PAYMENT", "DAYS_INSTALMENT"),
            ),
            FeatureSpec(
                "DBD",
                ("mean", "sum"),
                transform="positive_difference",
                source_columns=("DAYS_INSTALMENT", "DAYS_ENTRY_PAYMENT"),
            ),
            FeatureSpec(
                "PAYMENT_PERC",
                ("mean", "sum", "var"),
                transform="ratio",
                source_columns=("AMT_PAYMENT", "AMT_INSTALMENT"),
            ),
            FeatureSpec("AMT_INSTALMENT", ("mean", "sum")),
            FeatureSpec("AMT_PAYMENT", ("mean", "sum")),
            FeatureSpec("DAYS_ENTRY_PAYMENT", ("mean", "sum")),
        ),
        client_preparation=(
            "apply positive clipping for DPD and DBD",
            "calculate PAYMENT_PERC",
            "compact missing numeric values",
            "align rows to anonymous applicant indices",
        ),
        excluded_outputs=("min", "max", "nunique", "encrypted division", "encrypted clipping"),
    ),
    TaskSpec(
        task_id="I03",
        slug="payment_difference",
        function_name="installments_payments",
        title="Encrypted installment payment-difference moments",
        input_file="installments_payments.csv",
        source_lines="199-229",
        kind="difference_moments",
        kernel_ids=("K03",),
        features=(
            FeatureSpec(
                "PAYMENT_DIFF",
                ("mean", "sum", "var"),
                transform="difference",
                source_columns=("AMT_INSTALMENT", "AMT_PAYMENT"),
            ),
        ),
        client_preparation=(
            "compact rows where either amount is missing",
            "align rows to anonymous applicant indices",
        ),
        excluded_outputs=("max",),
    ),
)
