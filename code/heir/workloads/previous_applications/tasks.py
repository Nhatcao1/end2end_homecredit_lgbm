"""Previous-application general, approved, and refused definitions."""

from code.heir.workloads.grouped import FeatureSpec, TaskSpec


PREVIOUS_FEATURES = (
    FeatureSpec("AMT_ANNUITY", ("mean",)),
    FeatureSpec("AMT_APPLICATION", ("mean",)),
    FeatureSpec("AMT_CREDIT", ("mean",)),
    FeatureSpec(
        "APP_CREDIT_PERC",
        ("mean", "var"),
        transform="ratio",
        source_columns=("AMT_APPLICATION", "AMT_CREDIT"),
    ),
    FeatureSpec("AMT_DOWN_PAYMENT", ("mean",)),
    FeatureSpec("AMT_GOODS_PRICE", ("mean",)),
    FeatureSpec("HOUR_APPR_PROCESS_START", ("mean",)),
    FeatureSpec("RATE_DOWN_PAYMENT", ("mean",)),
    FeatureSpec("DAYS_DECISION", ("mean",)),
    FeatureSpec("CNT_PAYMENT", ("mean", "sum")),
)


def _task(task_id: str, slug: str, title: str, branch_value: str = "") -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        slug=slug,
        function_name="previous_applications",
        title=title,
        input_file="previous_application.csv",
        source_lines="132-175",
        kind="moments",
        kernel_ids=("K01", "K02"),
        features=PREVIOUS_FEATURES,
        branch_column="NAME_CONTRACT_STATUS" if branch_value else "",
        branch_value=branch_value,
        client_preparation=(
            "replace source sentinel values and compact missing values",
            "calculate APP_CREDIT_PERC before encryption",
            "derive and encrypt the contract-status branch mask",
            "align rows to anonymous applicant indices",
        ),
        excluded_outputs=("min", "max", "dynamic one-hot category discovery"),
    )


TASKS = (
    _task("P01", "general_aggregates", "Previous-application general aggregates"),
    _task("P02", "approved_aggregates", "Approved previous-application aggregates", "Approved"),
    _task("P03", "refused_aggregates", "Refused previous-application aggregates", "Refused"),
)
