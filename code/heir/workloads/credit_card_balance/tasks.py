"""Credit-card count and supported numeric-moment definitions."""

from code.heir.workloads.grouped import FeatureSpec, TaskSpec


NUMERIC_COLUMNS = (
    "MONTHS_BALANCE",
    "AMT_BALANCE",
    "AMT_CREDIT_LIMIT_ACTUAL",
    "AMT_DRAWINGS_ATM_CURRENT",
    "AMT_DRAWINGS_CURRENT",
    "AMT_DRAWINGS_OTHER_CURRENT",
    "AMT_DRAWINGS_POS_CURRENT",
    "AMT_INST_MIN_REGULARITY",
    "AMT_PAYMENT_CURRENT",
    "AMT_PAYMENT_TOTAL_CURRENT",
    "AMT_RECEIVABLE_PRINCIPAL",
    "AMT_RECIVABLE",
    "AMT_TOTAL_RECEIVABLE",
    "CNT_DRAWINGS_ATM_CURRENT",
    "CNT_DRAWINGS_CURRENT",
    "CNT_DRAWINGS_OTHER_CURRENT",
    "CNT_DRAWINGS_POS_CURRENT",
    "CNT_INSTALMENT_MATURE_CUM",
    "SK_DPD",
    "SK_DPD_DEF",
)


TASKS = (
    TaskSpec(
        task_id="C01",
        slug="credit_card_count",
        function_name="credit_card_balance",
        title="Credit-card balance row count",
        input_file="credit_card_balance.csv",
        source_lines="231-243",
        kind="count",
        kernel_ids=("K01",),
        client_preparation=("drop SK_ID_PREV and align anonymous applicant rows",),
        excluded_outputs=("min", "max"),
    ),
    TaskSpec(
        task_id="C02",
        slug="numeric_moments",
        function_name="credit_card_balance",
        title="Credit-card supported numeric aggregates",
        input_file="credit_card_balance.csv",
        source_lines="231-243",
        kind="moments",
        kernel_ids=("K01", "K02"),
        features=tuple(FeatureSpec(column, ("mean", "sum", "var")) for column in NUMERIC_COLUMNS),
        client_preparation=(
            "drop SK_ID_PREV",
            "compact missing numeric values",
            "align rows to anonymous applicant indices",
        ),
        excluded_outputs=("min", "max", "dynamic one-hot category discovery"),
    ),
)
