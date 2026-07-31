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
    ROOT / "code/openfhe_direct/benchmarks/synthetic_vnd/subtract.py",
)


class OpenFHEBenchmarkApiCommentTest(unittest.TestCase):
    def test_every_session_method_reference_has_adjacent_api_comment(self):
        for path in BENCHMARKS:
            source = path.read_text(encoding="utf-8")
            lines = source.splitlines()
            tree = ast.parse(source)
            references = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "session"
            ]
            self.assertTrue(references, path.name)
            for reference in references:
                preceding = lines[max(0, reference.lineno - 4):reference.lineno]
                self.assertTrue(
                    any("# HE API call:" in line for line in preceding),
                    f"{path.name}:{reference.lineno} session."
                    f"{reference.attr} lacks an adjacent HE API comment",
                )


if __name__ == "__main__":
    unittest.main()
