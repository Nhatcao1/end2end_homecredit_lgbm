import unittest

from code.openfhe_direct import HEWorkflow
from tests.test_openfhe_direct_credit_api import _OpenFHE


class OpenFHEDirectPlannerTest(unittest.TestCase):
    def test_payment_diff_plan_derives_depth_and_keys(self):
        workflow = HEWorkflow()
        installment = workflow.input("installment")
        payment = workflow.input("payment")
        difference = workflow.subtract(installment, payment)
        workflow.output("sum", workflow.sum(difference))
        workflow.output("mean", workflow.mean(difference))
        workflow.output("variance", workflow.variance(difference))

        fake = _OpenFHE()
        runtime = workflow.compile(slot_count=3, _openfhe_module=fake)
        plan = runtime.physical_plan

        self.assertEqual(2, plan.required_depth)
        self.assertTrue(plan.needs_eval_mult_key)
        self.assertTrue(plan.needs_eval_sum_key)
        self.assertEqual("FLEXIBLEAUTO", fake.parameters.scaling_technique)
        self.assertTrue(fake.context.mult_keys_generated)
        self.assertTrue(fake.context.sum_keys_generated)

    def test_front_end_evaluates_without_intermediate_plaintext(self):
        workflow = HEWorkflow()
        installment = workflow.input("installment")
        payment = workflow.input("payment")
        difference = workflow.subtract(installment, payment)
        workflow.output("difference", difference)
        workflow.output("sum", workflow.sum(difference))
        workflow.output("mean", workflow.mean(difference))
        workflow.output("variance", workflow.variance(difference))

        runtime = workflow.compile(
            slot_count=3,
            _openfhe_module=_OpenFHE(),
        )
        parents = runtime.encrypt_inputs(
            {
                "installment": [800.0, 500.0, 1000.0],
                "payment": [640.0, 600.0, 1000.0],
            }
        )
        results = runtime.evaluator.evaluate(parents)

        self.assertFalse(runtime.evaluator._session.can_decrypt)
        with self.assertRaisesRegex(RuntimeError, "cannot decrypt"):
            runtime.evaluator._session.decrypt(results["sum"])
        with self.assertRaisesRegex(RuntimeError, "cannot encrypt"):
            runtime.evaluator._session.encrypt([1.0, 2.0, 3.0])

        self.assertEqual(
            [160.0, -100.0, 0.0],
            runtime.decrypt(results["difference"]),
        )
        self.assertEqual(60.0, runtime.decrypt(results["sum"]))
        self.assertEqual(20.0, runtime.decrypt(results["mean"]))
        self.assertEqual(17200.0, runtime.decrypt(results["variance"]))

    def test_add_only_plan_does_not_generate_unused_keys(self):
        workflow = HEWorkflow()
        left = workflow.input("left")
        right = workflow.input("right")
        workflow.output("added", workflow.add(left, right))

        fake = _OpenFHE()
        runtime = workflow.compile(slot_count=3, _openfhe_module=fake)

        self.assertFalse(runtime.physical_plan.needs_eval_mult_key)
        self.assertFalse(runtime.physical_plan.needs_eval_sum_key)
        self.assertFalse(fake.context.mult_keys_generated)
        self.assertFalse(fake.context.sum_keys_generated)


if __name__ == "__main__":
    unittest.main()
