"""Registry for five public function benchmarks and their internal components."""

from code.heir.workloads.bureau_and_balance.tasks import TASKS as BUREAU_TASKS
from code.heir.workloads.credit_card_balance.tasks import TASKS as CREDIT_CARD_TASKS
from code.heir.workloads.grouped import FunctionSpec, TaskSpec
from code.heir.workloads.installments_payments.tasks import TASKS as INSTALLMENT_TASKS
from code.heir.workloads.pos_cash.tasks import TASKS as POS_TASKS
from code.heir.workloads.previous_applications.tasks import TASKS as PREVIOUS_TASKS


COMPONENTS: tuple[TaskSpec, ...] = (
    *BUREAU_TASKS,
    *PREVIOUS_TASKS,
    *POS_TASKS,
    *INSTALLMENT_TASKS,
    *CREDIT_CARD_TASKS,
)
COMPONENTS_BY_ID = {component.task_id: component for component in COMPONENTS}

if len(COMPONENTS) != 13 or len(COMPONENTS_BY_ID) != len(COMPONENTS):
    raise RuntimeError("the five function benchmarks must contain 13 unique components")


FUNCTIONS: tuple[FunctionSpec, ...] = (
    FunctionSpec(
        "BUREAU",
        "bureau",
        "bureau_and_balance",
        "Bureau and bureau-balance feature engineering",
        "bureau.csv",
        "74-129",
        BUREAU_TASKS,
    ),
    FunctionSpec(
        "PREVIOUS",
        "previous",
        "previous_applications",
        "Previous-application feature engineering",
        "previous_application.csv",
        "132-175",
        PREVIOUS_TASKS,
    ),
    FunctionSpec(
        "POS",
        "pos",
        "pos_cash",
        "POS cash feature engineering",
        "POS_CASH_balance.csv",
        "178-196",
        POS_TASKS,
    ),
    FunctionSpec(
        "INSTALLMENTS",
        "installments",
        "installments_payments",
        "Installment-payment feature engineering",
        "installments_payments.csv",
        "199-229",
        INSTALLMENT_TASKS,
    ),
    FunctionSpec(
        "CREDIT_CARD",
        "credit_card",
        "credit_card_balance",
        "Credit-card balance feature engineering",
        "credit_card_balance.csv",
        "231-243",
        CREDIT_CARD_TASKS,
    ),
)
FUNCTIONS_BY_NAME = {function.name: function for function in FUNCTIONS}
FUNCTIONS_BY_NAME.update({function.benchmark_id.lower(): function for function in FUNCTIONS})


def get_function(name: str) -> FunctionSpec:
    try:
        return FUNCTIONS_BY_NAME[name.lower()]
    except KeyError as error:
        choices = ", ".join(function.name for function in FUNCTIONS)
        raise ValueError(f"unknown function benchmark: {name}; choose {choices}") from error
