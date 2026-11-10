import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from custom.reco.frame_unchanged import FrameUnchanged, RememberFrame

LIST = [100, 100, 200, 100]
TABS = [100, 20, 200, 40]
PARAM = json.dumps({"regions": [LIST, TABS], "max_changed_ratio": 0.15})


def frame():
    return np.full((720, 1280, 3), 120, dtype=np.uint8)


def paint(image, region, share):
    x, y, w, h = region
    image[y:y + int(h * share), x:x + w] = 240
    return image


class FrameUnchangedTest(unittest.TestCase):
    def remember(self, image, task_id=1):
        context = Mock()
        context.tasker.controller.cached_image = image
        result = RememberFrame().run(context, SimpleNamespace(task_detail=SimpleNamespace(task_id=task_id)))
        self.assertTrue(result.success)

    def analyze(self, image, task_id=1, param=PARAM):
        arg = SimpleNamespace(task_detail=SimpleNamespace(task_id=task_id), image=image, custom_recognition_param=param)
        return FrameUnchanged().analyze(Mock(), arg)

    def test_small_animation_noise_counts_as_bottom(self):
        self.remember(frame())
        result = self.analyze(paint(frame(), LIST, 0.05))
        self.assertEqual(list(result.box), LIST)

    def test_scrolled_list_is_not_bottom(self):
        self.remember(frame())
        self.assertIsNone(self.analyze(paint(frame(), LIST, 0.36)))

    def test_switched_tab_is_not_bottom_even_if_list_matches(self):
        self.remember(frame())
        self.assertIsNone(self.analyze(paint(frame(), TABS, 0.44)))

    def test_baseline_is_a_copy_of_the_frame(self):
        image = frame()
        self.remember(image)
        image[:] = 0
        self.assertIsNotNone(self.analyze(frame()))

    def test_baseline_from_another_task_is_ignored(self):
        self.remember(frame(), task_id=1)
        self.assertIsNone(self.analyze(frame(), task_id=2))

    def test_missing_frame_never_reports_bottom(self):
        self.remember(None)
        self.assertIsNone(self.analyze(frame()))

    def test_invalid_parameters_never_report_bottom(self):
        self.remember(frame())
        with self.assertLogs("MaaOnmyoji.frame_unchanged", level="ERROR"):
            self.assertIsNone(self.analyze(frame(), param=json.dumps({"regions": [LIST]})))


if __name__ == "__main__":
    unittest.main()
