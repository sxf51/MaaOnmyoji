import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from custom.action.general import NodeOverride  # noqa: E402


class OverrideFailureTest(unittest.TestCase):
    def test_pipeline_override_failure_is_reported(self):
        context = Mock()
        context.override_pipeline.return_value = False
        result = NodeOverride().run(context, Mock(custom_action_param={"Node": {"enabled": False}}))
        self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
