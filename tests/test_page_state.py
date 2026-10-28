import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from custom import page_state


class PageStateTest(unittest.TestCase):
    def test_false_hit_is_not_a_match(self):
        self.assertFalse(page_state.hit(SimpleNamespace(hit=False, box=[0, 0, 1, 1])))

    def test_overlay_precedes_background(self):
        context = Mock()
        context.run_recognition.side_effect = lambda name, image: SimpleNamespace(
            hit=name in {"PageRaid", "PageRaidTarget"}, box=[0, 0, 1, 1])
        self.assertEqual(page_state.detect_page(context, object()), "RaidTarget")

    def test_page_detection_uses_each_supplied_frame_without_input(self):
        context = Mock()
        context.run_recognition.side_effect = lambda name, image: SimpleNamespace(
            hit=name == "Page" + image, box=[0, 0, 1, 1])
        self.assertEqual(page_state.detect_page(context, "Home"), "Home")
        self.assertEqual(page_state.detect_page(context, "Explore"), "Explore")
        context.run_action.assert_not_called()

    def test_cooperative_invitation_masks_every_background_page(self):
        for page in page_state.PAGES:
            if page in ("NoResource", "Bounty"):
                continue
            with self.subTest(page=page):
                context = Mock()
                context.run_recognition.side_effect = lambda name, image: SimpleNamespace(
                    hit=name in {"PageBounty", "Page" + page}, box=[0, 0, 1, 1])
                self.assertEqual(page_state.detect_page(context, object()), "Bounty")
                context.run_task.assert_not_called()
