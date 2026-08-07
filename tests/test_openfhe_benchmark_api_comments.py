import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = (
    ROOT / "code/openfhe_direct/benchmarks/api_latency.py",
    ROOT / "code/openfhe_direct/benchmarks/single_function.py",
    ROOT / "code/openfhe_direct/benchmarks/payment_diff_sum_mean.py",
    ROOT / "code/openfhe_direct/benchmarks/primitives.py",
    ROOT / "code/openfhe_direct/benchmarks/payment_diff_multigroup.py",
    ROOT / "code/openfhe_direct/benchmarks/synthetic_vnd/sum.py",
    ROOT / "code/openfhe_direct/benchmarks/synthetic_vnd/ckks_sum.py",
    ROOT
    / "code/openfhe_direct/benchmarks/synthetic_numeric/"
    "multiplication_limits.py",
)

FORBIDDEN_HE_CLI_FLAGS = (
    "--ring-dimension",
    "--multiplicative-depth",
    "--scaling-mod-size",
    "--first-mod-size",
    "--plaintext-modulus-bits",
    "--slot-count",
    "--input-scale",
    "--normalization-divisor",
    "--absolute-tolerance",
    "--absolute-tolerance-vnd",
    "--relative-tolerance",
)


class OpenFHEBenchmarkApiCommentTest(unittest.TestCase):
    def test_normal_benchmark_clis_do_not_expose_he_parameters(self):
        for path in BENCHMARKS:
            source = path.read_text(encoding="utf-8")
            for flag in FORBIDDEN_HE_CLI_FLAGS:
                self.assertNotIn(
                    f'add_argument("{flag}"',
                    source,
                    f"{path.name} exposes backend parameter {flag}",
                )

    def test_every_role_method_reference_has_adjacent_api_comment(self):
        for path in BENCHMARKS:
            source = path.read_text(encoding="utf-8")
            lines = source.splitlines()
            tree = ast.parse(source)
            references = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in {"client", "evaluator", "session"}
                and node.attr not in {"evaluator_view"}
            ]
            self.assertTrue(references, path.name)
            for reference in references:
                preceding = lines[max(0, reference.lineno - 4):reference.lineno]
                self.assertTrue(
                    any("# HE API call:" in line for line in preceding),
                    f"{path.name}:{reference.lineno} {reference.value.id}."
                    f"{reference.attr} lacks an adjacent HE API comment",
                )


if __name__ == "__main__":
    unittest.main()
