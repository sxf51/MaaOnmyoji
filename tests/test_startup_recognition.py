import ctypes
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from maa.define import MaaBool, MaaControllerHandle, MaaCtrlId, MaaStringBufferHandle

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from custom.reco.startup import (  # noqa: E402
    StartupHomeIdentity,
    _ensure_shell_api_signatures,
    get_cached_nickname,
    reset_startup_identity_cache_for_test,
)


def ocr_detail(text: str, box: list[int]) -> SimpleNamespace:
    result = SimpleNamespace(text=text)
    return SimpleNamespace(
        hit=True,
        box=box,
        best_result=result,
        filtered_results=[result],
    )


def make_context(device_time: str = "12:35") -> Mock:
    date_job = Mock(succeeded=True)
    date_job.wait.return_value = date_job
    date_job.get.return_value = device_time
    controller = Mock(uuid="controller-1")
    controller.post_shell.return_value = date_job
    context = Mock()
    context.tasker.controller = controller
    return context


def make_argv() -> Mock:
    return Mock(image=object())


class StartupHomeIdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_startup_identity_cache_for_test()

    def test_matching_time_caches_nickname(self) -> None:
        context = make_context()
        context.run_recognition.side_effect = [
            ocr_detail("12:34", [198, 5, 67, 22]),
            ocr_detail("测试昵称", [124, 28, 172, 29]),
        ]

        result = StartupHomeIdentity().analyze(context, make_argv())

        self.assertIsNotNone(result)
        self.assertEqual(get_cached_nickname("controller-1"), "测试昵称")
        context.tasker.controller.post_shell.assert_called_once_with(
            "date +%H:%M", timeout=3000
        )

    def test_cached_nickname_is_not_recognized_again(self) -> None:
        context = make_context("08:00")
        context.run_recognition.side_effect = [
            ocr_detail("08:00", [198, 5, 67, 22]),
            ocr_detail("一次昵称", [124, 28, 172, 29]),
            ocr_detail("08:01", [198, 5, 67, 22]),
        ]
        recognition = StartupHomeIdentity()

        self.assertIsNotNone(recognition.analyze(context, make_argv()))
        self.assertIsNotNone(recognition.analyze(context, make_argv()))

        names = [call.args[0] for call in context.run_recognition.call_args_list]
        self.assertEqual(names.count("StartupHomeNicknameOCR"), 1)

    def test_time_difference_over_one_minute_fails_before_nickname(self) -> None:
        context = make_context("12:36")
        context.run_recognition.return_value = ocr_detail(
            "12:34", [198, 5, 67, 22]
        )

        result = StartupHomeIdentity().analyze(context, make_argv())

        self.assertIsNone(result)
        context.run_recognition.assert_called_once()
        self.assertIsNone(get_cached_nickname("controller-1"))

    def test_midnight_difference_is_cyclic(self) -> None:
        context = make_context("00:00")
        context.run_recognition.side_effect = [
            ocr_detail("23:59", [198, 5, 67, 22]),
            ocr_detail("跨日昵称", [124, 28, 172, 29]),
        ]

        self.assertIsNotNone(StartupHomeIdentity().analyze(context, make_argv()))

    def test_time_is_matched_inside_extra_ocr_characters(self) -> None:
        context = make_context("12:35")
        context.run_recognition.side_effect = [
            ocr_detail("OCR912:36extra7", [198, 5, 67, 22]),
            ocr_detail("nickname", [124, 28, 172, 29]),
        ]

        self.assertIsNotNone(StartupHomeIdentity().analyze(context, make_argv()))

    def test_previous_minute_wraps_across_midnight_with_extra_text(self) -> None:
        context = make_context("00:00")
        context.run_recognition.side_effect = [
            ocr_detail("time=23:59!", [198, 5, 67, 22]),
            ocr_detail("nickname", [124, 28, 172, 29]),
        ]

        self.assertIsNotNone(StartupHomeIdentity().analyze(context, make_argv()))

    def test_missing_python_binding_shell_signatures_are_filled(self) -> None:
        framework = SimpleNamespace(
            MaaControllerPostShell=Mock(),
            MaaControllerGetShellOutput=Mock(),
        )

        with patch(
            "custom.reco.startup.Library.framework", return_value=framework
        ):
            _ensure_shell_api_signatures()

        self.assertEqual(framework.MaaControllerPostShell.restype, MaaCtrlId)
        self.assertEqual(
            framework.MaaControllerPostShell.argtypes,
            [MaaControllerHandle, ctypes.c_char_p, ctypes.c_int64],
        )
        self.assertEqual(framework.MaaControllerGetShellOutput.restype, MaaBool)
        self.assertEqual(
            framework.MaaControllerGetShellOutput.argtypes,
            [MaaControllerHandle, MaaStringBufferHandle],
        )


if __name__ == "__main__":
    unittest.main()
