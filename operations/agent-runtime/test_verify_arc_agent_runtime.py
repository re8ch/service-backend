import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ArcAgentRuntimeContractTest(unittest.TestCase):
    def test_contract(self):
        result = subprocess.run(
            [sys.executable, "operations/agent-runtime/verify_arc_agent_runtime.py"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("PASS ephemeral R640 ARC contract", result.stdout)


if __name__ == "__main__":
    unittest.main()

