"""Channel option and installed-package fallback for starting/stopping the game."""
import json
import unittest
from pathlib import Path

import sys
from tools.validate_schema import load_jsonc
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom.action.game_package import OFFICIAL, choose_package, matching_packages, parse_packages

ROOT = Path(__file__).resolve().parents[1]


class ChoosePackageTest(unittest.TestCase):
    def test_parse_pm_list_output(self):
        output = "package:com.android.settings\r\npackage:com.netease.onmyoji.mi\n\nnoise\n"
        self.assertEqual(parse_packages(output), ["com.android.settings", "com.netease.onmyoji.mi"])

    def test_only_the_onmyoji_prefix_matches(self):
        installed = ["com.netease.onmyoji", "com.netease.onmyoji.bili", "com.netease.onmyojix", "com.netease.cloudmusic"]
        self.assertEqual(matching_packages(installed), ["com.netease.onmyoji", "com.netease.onmyoji.bili"])

    def test_selected_channel_is_used_when_installed(self):
        installed = [OFFICIAL, "com.netease.onmyoji.mi"]
        self.assertEqual(choose_package(installed, "com.netease.onmyoji.mi"), ("com.netease.onmyoji.mi", "selected"))

    def test_missing_or_unselected_channel_matches_installed_package(self):
        self.assertEqual(choose_package(["com.netease.onmyoji.huawei"], "com.netease.onmyoji.mi"),
                         ("com.netease.onmyoji.huawei", "matched"))
        self.assertEqual(choose_package(["com.netease.onmyoji.bili"], ""), ("com.netease.onmyoji.bili", "matched"))

    def test_several_matches_prefer_official_then_netease(self):
        self.assertEqual(choose_package(["com.netease.onmyoji.bili", OFFICIAL, "com.netease.onmyoji"], ""), (OFFICIAL, "matched"))
        self.assertEqual(choose_package(["com.netease.onmyoji.bili", "com.netease.onmyoji"], ""), ("com.netease.onmyoji", "matched"))
        self.assertEqual(choose_package(["com.netease.onmyoji.mi", "com.netease.onmyoji.bili"], ""), ("com.netease.onmyoji.bili", "matched"))

    def test_nothing_found_falls_back_to_selection_or_official(self):
        self.assertEqual(choose_package([], "com.netease.onmyoji.mi"), ("com.netease.onmyoji.mi", "fallback"))
        self.assertEqual(choose_package([], ""), (OFFICIAL, "fallback"))


class ChannelOptionTest(unittest.TestCase):
    def test_launch_and_stop_stay_native(self):
        startup = load_jsonc(ROOT / "assets/resource/base/pipeline/Startup.json")
        shutdown = load_jsonc(ROOT / "assets/resource/base/pipeline/Shutdown.json")
        for nodes, entry, target, action in ((startup, "StartupTask", "StartupLaunch", "StartApp"),
                                             (shutdown, "ShutdownTask", "ShutdownStop", "StopApp")):
            param = nodes[entry]["action"]["param"]
            self.assertEqual(param["custom_action"], "ResolveGamePackage")
            self.assertEqual(param["custom_action_param"], {"target": target, "action": action})
            self.assertEqual(nodes[entry]["attach"], {"package": ""})
            self.assertEqual(nodes[entry]["next"][-3:], [target, "[JumpBack]CommonBountyInterrupt", "CommonUnknownPopup"])
            self.assertEqual(nodes[target]["action"], {"type": action, "param": {"package": OFFICIAL}})
        self.assertEqual(startup["StartupLaunch"]["on_error"], ["StartupLaunchError"])

    def test_both_tasks_share_the_channel_option(self):
        startup = load_jsonc(ROOT / "assets/tasks/Startup.json")
        shutdown = load_jsonc(ROOT / "assets/tasks/Shutdown.json")
        self.assertEqual(startup["task"][0]["option"][0], "游戏渠道")
        self.assertEqual(shutdown["task"][0]["option"], ["游戏渠道"])
        option = startup["option"]["游戏渠道"]
        self.assertEqual(option["default_case"], "自动")
        packages = {}
        for case in option["cases"]:
            override = case["pipeline_override"]
            self.assertEqual(override["StartupTask"]["attach"], override["ShutdownTask"]["attach"])
            packages[case["name"]] = override["StartupTask"]["attach"]["package"]
        self.assertEqual(packages["自动"], "")
        self.assertEqual(packages["官服"], OFFICIAL)
        for name, package in packages.items():
            if package:
                self.assertTrue(package == "com.netease.onmyoji" or package.startswith("com.netease.onmyoji."), name)


if __name__ == "__main__":
    unittest.main()
