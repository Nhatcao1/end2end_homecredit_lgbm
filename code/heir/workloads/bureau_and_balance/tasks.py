"""Bureau general, active, and closed benchmark definitions."""

from code.heir.workloads.grouped import FeatureSpec, TaskSpec


BUREAU_FEATURES = (
    FeatureSpec("DAYS_CREDIT", ("mean", "var")),
    FeatureSpec("DAYS_CREDIT_ENDDATE", ("mean",)),
    FeatureSpec("DAYS_CREDIT_UPDATE", ("mean",)),
    FeatureSpec("CREDIT_DAY_OVERDUE", ("mean",)),
    FeatureSpec("AMT_CREDIT_MAX_OVERDUE", ("mean",)),
    FeatureSpec("AMT_CREDIT_SUM", ("mean", "sum")),
    FeatureSpec("AMT_CREDIT_SUM_DEBT", ("mean", "sum")),
    FeatureSpec("AMT_CREDIT_SUM_OVERDUE", ("mean",)),
    FeatureSpec("AMT_CREDIT_SUM_LIMIT", ("mean", "sum")),
    FeatureSpec("AMT_ANNUITY", ("mean",)),
    FeatureSpec("CNT_CREDIT_PROLONG", ("sum",)),
    FeatureSpec(
        "MONTHS_BALANCE_SIZE",
        ("mean", "sum"),
        transform="bureau_balance_size",
        source_columns=("SK_ID_BUREAU",),
    ),
)


def _task(task_id: str, slug: str, title: str, branch_value: str = "") -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        slug=slug,
        function_name="bureau_and_balance",
        title=title,
        input_file="bureau.csv",
        source_lines="74-129",
        kind="moments",
        kernel_ids=("K01", "K02"),
        features=BUREAU_FEATURES,
        branch_column="CREDIT_ACTIVE" if branch_value else "",
        branch_value=branch_value,
        client_preparation=(
            "select and align SK_ID_CURR/SK_ID_BUREAU rows",
            "compact missing numeric values before encryption",
            "derive and encrypt the CREDIT_ACTIVE branch mask",
            "count bureau_balance rows per SK_ID_BUREAU",
        ),
        excluded_outputs=("min", "max", "dynamic one-hot category discovery"),
    )


TASKS = (
    _task("B01", "general_aggregates", "Bureau general supported aggregates"),
    _task("B02", "active_aggregates", "Bureau active-credit aggregates", "Active"),
    _task("B03", "closed_aggregates", "Bureau closed-credit aggregates", "Closed"),
)
