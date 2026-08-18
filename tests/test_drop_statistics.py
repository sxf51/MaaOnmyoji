import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from custom.action.drop_statistics import (  # noqa: E402
    AsyncReportQueue,
    DropStatisticsState,
    merge_payloads,
    recognize_drops,
    template_label,
    template_match_count,
)


class DropStatisticsTest(unittest.TestCase):
    def setUp(self) -> None:
        DropStatisticsState.reset()

    def test_template_variant_uses_same_label(self) -> None:
        self.assertEqual(template_label(Path("招财猫@亮色.png")), "招财猫")

    def test_template_match_count_uses_filtered_results(self) -> None:
        detail = SimpleNamespace(hit=True, filtered_results=[object(), object(), object()])
        self.assertEqual(template_match_count(detail), 3)

    def test_multiple_variants_take_max_count(self) -> None:
        class FakeContext:
            def __init__(self) -> None:
                self.counts = iter([2, 3, 1])

            def run_recognition_direct(self, reco_type, reco_param, image):
                del reco_type, reco_param, image
                count = next(self.counts)
                return SimpleNamespace(hit=bool(count), filtered_results=[object()] * count)

        drops = recognize_drops(
            FakeContext(),
            object(),
            {"招财猫": ["a.png", "b.png"], "蚌精": ["c.png"]},
        )
        self.assertEqual(drops, Counter({"招财猫": 3, "蚌精": 1}))

    def test_state_accumulates_battles(self) -> None:
        DropStatisticsState.add(Counter({"招财猫": 2}))
        DropStatisticsState.add(Counter({"招财猫": 1, "青吉鬼": 1}))
        self.assertEqual(DropStatisticsState.battles, 2)
        self.assertEqual(DropStatisticsState.summary(), {"招财猫": 3, "青吉鬼": 1})

    def test_report_queue_sends_in_background_and_flushes(self) -> None:
        sent = []

        def sender(url, payload):
            sent.append((url, payload))
            return True

        reporter = AsyncReportQueue(sender=sender)
        reporter.enqueue("https://example.invalid/report", {"battle_index": 1})

        self.assertTrue(reporter.flush(timeout=1.0))
        self.assertEqual(
            sent,
            [("https://example.invalid/report", {"battle_index": 1})],
        )
        self.assertTrue(reporter.shutdown(timeout=1.0))

    def test_report_queue_has_bounded_capacity(self) -> None:
        reporter = AsyncReportQueue(sender=lambda url, payload: True)
        self.assertEqual(reporter._queue.maxsize, 100)

    def test_merge_payloads_accumulates_drops(self) -> None:
        merged = merge_payloads(
            [
                {"battle_index": 1, "drops": {"招财猫": 2}, "recorded_at": "a"},
                {"battle_index": 2, "drops": {"招财猫": 1, "蚌精": 1}, "recorded_at": "b"},
            ]
        )
        self.assertEqual(merged["battle_count"], 2)
        self.assertEqual(merged["battle_indexes"], [1, 2])
        self.assertEqual(merged["drops"], {"招财猫": 3, "蚌精": 1})

    def test_failed_report_is_spooled_and_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            spool = Path(directory) / "drops.jsonl"
            failed = AsyncReportQueue(sender=lambda url, payload: False, spool_path=spool)
            failed.enqueue("https://example.invalid/report", {"battle_index": 1, "drops": {}})
            self.assertTrue(failed.flush(timeout=1.0))
            self.assertTrue(spool.is_file())
            failed.shutdown(timeout=1.0)

            sent = []
            recovered = AsyncReportQueue(
                sender=lambda url, payload: sent.append((url, payload)) or True,
                spool_path=spool,
            )
            recovered.recover()
            self.assertTrue(recovered.flush(timeout=1.0))
            self.assertEqual(len(sent), 1)
            self.assertFalse(spool.exists())
            recovered.shutdown(timeout=1.0)


if __name__ == "__main__":
    unittest.main()
