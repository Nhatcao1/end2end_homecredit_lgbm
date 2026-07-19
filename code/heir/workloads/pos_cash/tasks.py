"""POS count and supported numeric-moment definitions."""

from code.heir.workloads.grouped import FeatureSpec, TaskSpec


TASKS = (
    TaskSpec(
        task_id="POS01",
        slug="pos_count",
        function_name="pos_cash",
        title="POS cash history count",
        input_file="POS_CASH_balance.csv",
        source_lines="178-196",
        kind="count",
        kernel_ids=("K01",),
        client_preparation=("align rows to anonymous applicant indices",),
        excluded_outputs=("max", "dynamic one-hot category discovery"),
    ),
    TaskSpec(
        task_id="POS02",
        slug="numeric_means",
        function_name="pos_cash",
        title="POS cash supported numeric means",
        input_file="POS_CASH_balance.csv",
        source_lines="178-196",
        kind="moments",
        kernel_ids=("K02",),
        features=(
            FeatureSpec("MONTHS_BALANCE", ("mean",)),
            FeatureSpec("SK_DPD", ("mean",)),
            FeatureSpec("SK_DPD_DEF", ("mean",)),
        ),
        client_preparation=(
            "compact missing numeric values",
            "align rows to anonymous applicant indices",
        ),
        excluded_outputs=("max", "dynamic one-hot category discovery"),
    ),
)
