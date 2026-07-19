"""Single registry for all approved function-specific benchmark tasks."""

from code.heir.workloads.bureau_and_balance.tasks import TASKS as BUREAU_TASKS
from code.heir.workloads.credit_card_balance.tasks import TASKS as CREDIT_CARD_TASKS
from code.heir.workloads.grouped import TaskSpec
from code.heir.workloads.installments_payments.tasks import TASKS as INSTALLMENT_TASKS
from code.heir.workloads.pos_cash.tasks import TASKS as POS_TASKS
from code.heir.workloads.previous_applications.tasks import TASKS as PREVIOUS_TASKS


TASKS: tuple[TaskSpec, ...] = (
    *BUREAU_TASKS,
    *PREVIOUS_TASKS,
    *POS_TASKS,
    *INSTALLMENT_TASKS,
    *CREDIT_CARD_TASKS,
)
TASKS_BY_ID = {task.task_id: task for task in TASKS}

if len(TASKS) != 13 or len(TASKS_BY_ID) != len(TASKS):
    raise RuntimeError("the approved benchmark catalog must contain 13 unique tasks")


def get_task(task_id: str) -> TaskSpec:
    try:
        return TASKS_BY_ID[task_id.upper()]
    except KeyError as error:
        raise ValueError(f"unknown task ID: {task_id}") from error
