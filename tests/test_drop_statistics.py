import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from custom.action.drop_statistics import (  # noqa: E402
    AsyncReportQueue,
    DropStatisticsState,
    merge_payloads,
    recognize_drops,
    template_label,
    template_match_count,
    commit_drop_statistics,
    RewardDropStatistics,
    load_drop_catalog,
    parse_quantity,
)


class DropStatisticsTest(unittest.TestCase):
    def setUp(self) -> None:
        DropStatisticsState.reset()

    def test_template_variant_uses_same_label(self) -> None:
        self.assertEqual(template_label(Path("招财猫@亮色.png")), "招财猫")

    def test_template_match_count_uses_filtered_results(self) -> None:
        detail = SimpleNamespace(hit=True, filtered_results=[object(), object(), object()])
        self.assertEqual(template_match_count(detail), 3)

    def test_variants_union_disjoint_boxes_and_deduplicate_overlap(self):
        def result(x, score=.95):
            return SimpleNamespace(box=[x,100,30,30],score=score)
        context=Mock()
        context.run_recognition_direct.side_effect=[
            SimpleNamespace(hit=True,filtered_results=[result(100),result(200)]),
            SimpleNamespace(hit=True,filtered_results=[result(101),result(300)]),
            SimpleNamespace(hit=True,filtered_results=[result(100,.8),result(400)])]
        self.assertEqual(recognize_drops(context,object(),{'招财猫':['a','b'],'蚌精':['c']}),
                         Counter({'招财猫':3,'蚌精':1}))

    def test_stack_quantity_ocr_is_bound_to_matched_icon(self):
        context=Mock()
        context.run_recognition_direct.side_effect=[
            SimpleNamespace(hit=True,filtered_results=[SimpleNamespace(box=[100,100,30,30],score=.95)]),
            SimpleNamespace(hit=True,filtered_results=[SimpleNamespace(text='120')])]
        drops=recognize_drops(context,np.zeros((720,1280,3)),{'金币':['coin']},
                              specs={'金币':{'quantity_roi':[0,28,50,25]}})
        self.assertEqual(drops,Counter({'金币':120}))

    def test_unreadable_quantity_is_not_guessed_as_one(self):
        context=Mock()
        context.run_recognition_direct.side_effect=[
            SimpleNamespace(hit=True,filtered_results=[SimpleNamespace(box=[100,100,30,30],score=.95)]),
            SimpleNamespace(hit=False)]
        self.assertEqual(recognize_drops(context,np.zeros((720,1280,3)),{'金币':['coin']},
            specs={'金币':{'quantity_roi':[0,28,50,25]}}),Counter({'金币（数量未识别）':1}))

    def test_integer_quantity_normalization_preserves_invalid_values(self):
        for text,count in [('.12',12),('１２',12),('×32',32),('x 2',2),('1.2',None),('-1',None),('0',None),('?',None)]:
            with self.subTest(text=text):
                self.assertEqual(parse_quantity(text),count)

    def test_optional_stack_reads_number_or_single_when_region_has_no_text(self):
        for results,expected in [([SimpleNamespace(text='32',score=.99)],32),([],1)]:
            with self.subTest(results=results):
                context=Mock()
                context.run_recognition_direct.side_effect=[
                    SimpleNamespace(hit=True,filtered_results=[SimpleNamespace(box=[100,100,108,108],score=.95)]),
                    SimpleNamespace(hit=bool(results),all_results=results)]
                image=np.full((720,1280,3),255 if results else 0,dtype=np.uint8)
                self.assertEqual(recognize_drops(context,image,{'材料':['a']},
                    specs={'材料':{'quantity_mode':'optional'}}),Counter({'材料':expected}))
                roi=list(context.run_recognition_direct.call_args.args[1].roi)
                self.assertGreater(roi[0],150)
                self.assertGreater(roi[1],170)

    def test_optional_stack_ocr_failure_or_unreadable_text_is_not_single(self):
        for result in [None,SimpleNamespace(hit=False,all_results=[SimpleNamespace(text='?',score=.9)]),
                       SimpleNamespace(hit=True,all_results=[SimpleNamespace(text='2',score=.2)])]:
            context=Mock()
            context.run_recognition_direct.side_effect=[
                SimpleNamespace(hit=True,filtered_results=[SimpleNamespace(box=[100,100,108,108],score=.95)]),result]
            self.assertEqual(recognize_drops(context,np.full((720,1280,3),255,dtype=np.uint8),{'材料':['a']},
                specs={'材料':{'quantity_mode':'optional'}}),Counter({'材料（数量未识别）':1}))

    def test_manual_templates_are_discovered_recursively_without_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'image'
            folder=root/'Rewards/Mitama/御魂'
            folder.mkdir(parents=True)
            (folder/'招财猫@亮.png').write_bytes(b'png')
            (folder/'招财猫@暗.png').write_bytes(b'png')
            with patch('custom.action.drop_statistics._IMAGE_ROOT',root), \
                 patch('custom.action.drop_statistics._TEMPLATE_ROOT',root/'Rewards'):
                templates,specs=load_drop_catalog('Mitama')
            self.assertEqual(len(templates['招财猫']),2)
            self.assertEqual(specs['招财猫']['quantity_mode'],'optional')
            self.assertTrue(all(name.startswith('Rewards/Mitama/') for name in templates['招财猫']))

    def test_manifest_does_not_hide_new_variants_and_supports_metadata_only(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'image'
            folder=root/'Rewards/Mitama'
            folder.mkdir(parents=True)
            for name in ['招财猫@亮.png','招财猫@暗.png','材料.png']:
                (folder/name).write_bytes(b'png')
            (folder/'catalog.json').write_text(json.dumps({'招财猫':{'templates':['招财猫@亮.png'],'threshold':.9},
                 '材料':{'quantity_mode':'required','quantity_roi':[50,70,45,30]}},ensure_ascii=False),'utf-8')
            with patch('custom.action.drop_statistics._IMAGE_ROOT',root), \
                 patch('custom.action.drop_statistics._TEMPLATE_ROOT',root/'Rewards'):
                templates,specs=load_drop_catalog('Mitama')
            self.assertEqual(len(templates['招财猫']),2)
            self.assertEqual(specs['材料']['quantity_mode'],'required')

    def test_catalog_schema_rejects_invalid_quantity_configuration(self):
        import json
        from jsonschema import Draft202012Validator
        from tools.validate_schema import is_reward_catalog
        schema=json.loads((Path(__file__).resolve().parents[1]/'tools/reward_catalog.schema.json').read_text('utf-8'))
        validator=Draft202012Validator(schema)
        self.assertFalse(list(validator.iter_errors({'材料':{'quantity_mode':'optional','quantity_roi':[55,75,50,30]}})))
        self.assertTrue(list(validator.iter_errors({'材料':{'quantity_mode':'guess','quantity_roi':[0,0,-1,30]}})))
        self.assertTrue(is_reward_catalog(Path('assets/resource/base/image/Rewards/Mitama/catalog.json')))
        self.assertFalse(is_reward_catalog(Path('assets/resource/base/pipeline/catalog.json')))

    def test_reports_from_different_tasks_are_not_merged(self):
        with self.assertRaises(ValueError):
            merge_payloads([{'task':'MitamaTask','drops':{'a':1}}, {'task':'AwakeningTask','drops':{'b':2}}])

    def test_repeated_settlement_is_staged_once_and_committed_once(self):
        context=Mock()
        context.tasker.controller.cached_image=np.zeros((720,1280,3))
        context.run_recognition.side_effect=lambda node,image:SimpleNamespace(hit=node=='DailyBattleResult')
        argv=SimpleNamespace(custom_action_param={'scope':'Awakening','result_node':'DailyBattleResult'})
        with patch('custom.action.drop_statistics.save_settlement'), \
             patch('custom.action.drop_statistics.load_drop_catalog',return_value=({'雷鼓':['x']},{})), \
             patch('custom.action.drop_statistics.recognize_drops',return_value=Counter({'雷鼓':3})) as reco:
            self.assertTrue(RewardDropStatistics().run(context,argv).success)
            self.assertTrue(RewardDropStatistics().run(context,argv).success)
            self.assertEqual(reco.call_count,1)
            self.assertEqual(DropStatisticsState.battles,0)
        with tempfile.TemporaryDirectory() as directory, \
             patch('custom.action.drop_statistics._PROJECT_ROOT',Path(directory)), \
             patch('custom.action.drop_statistics.get_runtime_params',return_value={}):
            commit_drop_statistics()
            commit_drop_statistics()
            self.assertEqual(DropStatisticsState.battles,1)
            self.assertEqual(DropStatisticsState.summary(),{'雷鼓':3})
            self.assertTrue((Path(directory)/'debug/rewards/Awakening-latest.json').is_file())

    def test_unstable_reward_region_never_saves_or_matches(self):
        context=Mock()
        context.wait_freezes.return_value=False
        argv=SimpleNamespace(custom_action_param={'scope':'Mitama','result_node':'DailyBattleResult'})
        with patch('custom.action.drop_statistics.save_settlement') as save, \
             patch('custom.action.drop_statistics.recognize_drops') as reco:
            self.assertFalse(RewardDropStatistics().run(context,argv).success)
            save.assert_not_called()
            reco.assert_not_called()
        self.assertIsNone(DropStatisticsState.pending)

    def test_statistics_reads_the_new_frame_after_native_stability_wait(self):
        context=Mock()
        early=np.zeros((720,1280,3),dtype=np.uint8)
        loaded=np.full((720,1280,3),5,dtype=np.uint8)
        context.tasker.controller.cached_image=early
        def settle(**kwargs):
            self.assertEqual(kwargs['wait_freezes_param'].time,1000)
            context.tasker.controller.cached_image=loaded
            return True
        context.wait_freezes.side_effect=settle
        context.run_recognition.side_effect=lambda node,image:SimpleNamespace(hit=node=='DailyBattleResult')
        argv=SimpleNamespace(custom_action_param={'scope':'Mitama','result_node':'DailyBattleResult'})
        with patch('custom.action.drop_statistics.save_settlement') as save, \
             patch('custom.action.drop_statistics.load_drop_catalog',return_value=({'券':['a']},{})), \
             patch('custom.action.drop_statistics.recognize_drops',return_value=Counter({'券':2})) as reco:
            self.assertTrue(RewardDropStatistics().run(context,argv).success)
            self.assertIs(save.call_args.args[1],loaded)
            self.assertIs(reco.call_args.args[1],loaded)

    def test_failure_or_overlay_never_collects_drops(self):
        context=Mock()
        context.tasker.controller.cached_image=np.zeros((720,1280,3))
        context.run_recognition.return_value=SimpleNamespace(hit=True)
        argv=SimpleNamespace(custom_action_param={'scope':'Mitama','result_node':'DailyBattleResult'})
        with patch('custom.action.drop_statistics.recognize_drops') as reco:
            self.assertFalse(RewardDropStatistics().run(context,argv).success)
            reco.assert_not_called()
        self.assertIsNone(DropStatisticsState.pending)

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
