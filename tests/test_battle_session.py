"""Failure cleanup keeps the original outcome and respects all stop requests."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom.battle_session import run_battle_session


def detail(ok):
    return SimpleNamespace(status=SimpleNamespace(succeeded=ok))


class BattleSessionTest(unittest.TestCase):
    def context(self, enabled=True, stopped=False):
        c=Mock()
        c.tasker.stopping=stopped
        c.get_node_data.return_value={'enabled':enabled}
        c.run_task.return_value=detail(False)
        c.clone.return_value.run_task.return_value=detail(True)
        return c

    def test_failed_battle_cleans_both_scopes_and_still_fails(self):
        for scope in ('Mitama','Awakening'):
            c=self.context()
            limits={'NativeNavCancelNoResource':{'max_hit':3}}
            self.assertFalse(run_battle_session(c,{'scope':scope,'cleanup_override':limits}))
            c.clone.return_value.run_task.assert_called_once()
            entry, overrides=c.clone.return_value.run_task.call_args.args
            self.assertEqual(entry,scope+'AbortCleanup')
            self.assertEqual(overrides[scope+'DisableBuff'],{'enabled':True})
            self.assertEqual(overrides['NativeNavCancelNoResource']['max_hit'],3)

    def test_disabled_buff_does_not_cleanup(self):
        c=self.context(enabled=False)
        self.assertFalse(run_battle_session(c,{'scope':'Mitama'}))
        c.clone.assert_not_called()

    def test_user_or_safety_stop_does_not_cleanup(self):
        c=self.context(stopped=True)
        self.assertFalse(run_battle_session(c,{'scope':'Awakening'}))
        c.clone.assert_not_called()

    def test_success_uses_normal_pipeline_cleanup_only(self):
        c=self.context();c.run_task.return_value=detail(True)
        self.assertTrue(run_battle_session(c,{'scope':'Awakening'}))
        c.clone.assert_not_called()

    def test_exception_and_cleanup_failure_preserve_original_failure(self):
        c=self.context();c.run_task.side_effect=RuntimeError('battle failed')
        c.clone.return_value.run_task.return_value=detail(False)
        self.assertFalse(run_battle_session(c,{'scope':'Mitama'}))
        c.clone.return_value.run_task.assert_called_once()

    def test_invalid_scope_cannot_run_arbitrary_entries(self):
        c=self.context()
        with self.assertRaises(ValueError):run_battle_session(c,{'scope':'Unknown'})
        c.run_task.assert_not_called()


if __name__=='__main__':unittest.main()
