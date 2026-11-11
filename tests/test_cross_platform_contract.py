import json
import unittest
from pathlib import Path


class CrossPlatformContractTest(unittest.TestCase):
    def test_contract_has_explicit_allowed_state_values(self):
        path = Path(__file__).parent / "fixtures" / "diagnostic_contract.json"
        contract = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("replay", contract["modes"])
        self.assertIn("reconnecting", contract["statuses"])
        self.assertEqual(contract["required_fields"][0], "mode")


if __name__ == "__main__":
    unittest.main()
