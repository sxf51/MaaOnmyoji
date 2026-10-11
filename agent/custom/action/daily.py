"""Bounded exploration, realm raid and reward flows using calibrated probes."""
from __future__ import annotations

import logging
import time

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction

from custom.action.target_count import _parse_params, get_runtime_params
from custom.navigation import act, detect_page, hit, navigate, pause, screenshot

logger = logging.getLogger("MaaOnmyoji.daily")


def click(context, x, y):
    if context.tasker.stopping:
        return False
    return context.tasker.controller.post_click(x, y).wait().succeeded and pause(context, 1.2)


def swipe(context, start, end):
    if context.tasker.stopping:
        return False
    return context.tasker.controller.post_swipe(*start, *end, 500).wait().succeeded and pause(context, 1)


def wait_page(context, pages, timeout=20):
    deadline = time.monotonic() + timeout
    while not context.tasker.stopping and time.monotonic() < deadline:
        image = screenshot(context)
        if image is None:
            return None
        if detect_page(context, image) in pages:
            return image
        if not pause(context, .5):
            break
    return None


def battle(context, destination, timeout=180):
    """Count only after seeing settlement and returning to the expected page."""
    deadline = time.monotonic() + timeout
    settled = False
    failed = False
    while not context.tasker.stopping and time.monotonic() < deadline:
        image = screenshot(context)
        if image is None:
            return "error"
        if hit(context.run_recognition("DailyNoResource", image)):
            logger.info("体力或挑战券不足，结束当前任务")
            return "exhausted"
        # Destination is checked independently; battle animations can obscure OCR.
        if settled and hit(context.run_recognition("Page" + destination, image)):
            return "failed" if failed else "success"
        if hit(context.run_recognition("DailyBattleFailed", image)):
            failed = True
        if act(context, "DailyBattleResult", image):
            settled = True
        elif act(context, "DailyPrepare", image):
            pass
        elif act(context, "DailyRewardPopup", image):
            pass
        elif not pause(context, .7):
            break
    logger.error("等待战斗结算超时或任务被停止")
    return "error"


def enter_chapter(context):
    if not navigate(context, ["Explore", "Chapter", "ChapterField"]):
        return False
    image = screenshot(context)
    if image is None:
        return False
    page = detect_page(context, image)
    if page == "ChapterField":
        return True
    if page == "Explore":
        for scan in range(24):
            if act(context, "ExplorationSelectChapter", image):
                break
            # Search in both directions: the list retains the user's last scroll.
            start, end = ((1170, 504), (1170, 250)) if scan < 12 else ((1170, 250), (1170, 504))
            if not swipe(context, start, end):
                return False
            image = screenshot(context)
            if image is None or not hit(context.run_recognition("PageExplore", image)):
                return False
        else:
            logger.error("未找到配置章节，请先手动打开章节详情或检查章节配置")
            return False
        image = wait_page(context, ["Chapter"])
        if image is None:
            return False
    if not act(context, "ExplorationEnterChapter", image):
        return False
    return wait_page(context, ["ChapterField"]) is not None


@AgentServer.custom_action("ExploreBattles")
class ExploreBattles(CustomAction):
    def run(self, context, argv):
        params = get_runtime_params()
        try:
            count = int(params.get("target_count", 10))
            if not 1 <= count <= 9999:
                raise ValueError("探索次数应在 1..9999")
        except (TypeError, ValueError) as exc:
            logger.error("探索配置：%s", exc)
            return CustomAction.RunResult(success=False)
        if not enter_chapter(context):
            return CustomAction.RunResult(success=False)
        for completed in range(count):
            found = False
            # A cleared chapter is exited and reentered once; never swipe forever.
            for attempt in range(2):
                for scan in range(7):
                    image = screenshot(context)
                    if image is None or context.tasker.stopping:
                        return CustomAction.RunResult(success=False)
                    if act(context, "ExplorationFindEnemy", image):
                        found = True
                        break
                    if not hit(context.run_recognition("PageChapterField", image)):
                        return CustomAction.RunResult(success=False)
                    if scan < 6 and not swipe(context, (1050, 470), (340, 470)):
                        return CustomAction.RunResult(success=False)
                if found:
                    break
                if attempt == 0 and (not navigate(context, ["Chapter"]) or not enter_chapter(context)):
                    return CustomAction.RunResult(success=False)
            if not found:
                logger.error("未找到可挑战怪物，停止探索")
                return CustomAction.RunResult(success=False)
            result = battle(context, "ChapterField")
            if result == "exhausted":
                return CustomAction.RunResult(success=True)
            if result != "success":
                return CustomAction.RunResult(success=False)
            progress = context.run_action("ExplorationProgress")
            if progress is None or not progress.success:
                return CustomAction.RunResult(success=False)
            logger.info("探索完成：%d/%d", completed + 1, count)
        return CustomAction.RunResult(success=True)


RAID_CARDS = [(148 + col * 332, 146 + row * 135, 317, 120)
              for row in range(3) for col in range(3)]


def raid_card_available(context, image, card):
    # Both probes are necessary: a missing stamp alone is not evidence of a card.
    if not hit(context.run_recognition("RealmRaidCard", image, {
        "RealmRaidCard": {"recognition": {"param": {"roi": card}}}
    })):
        return False
    return not raid_card_broken(context, image, card)


def raid_card_broken(context, image, card):
    return hit(context.run_recognition("RealmRaidBroken", image, {
        "RealmRaidBroken": {"recognition": {"param": {"roi": card}}}
    }))


@AgentServer.custom_action("RaidBattles")
class RaidBattles(CustomAction):
    def run(self, context, argv):
        try:
            params = _parse_params(argv.custom_action_param)
            limit = int(params.get("target_count", 9))
            if not 1 <= limit <= 9:
                raise ValueError("突破次数应在 1..9")
        except (ValueError, TypeError) as exc:
            logger.error("突破配置：%s", exc)
            return CustomAction.RunResult(success=False)
        if not navigate(context, ["Raid"]):
            return CustomAction.RunResult(success=False)
        completed = 0
        for card in RAID_CARDS:
            image = wait_page(context, ["Raid"])
            if image is None:
                return CustomAction.RunResult(success=False)
            if not raid_card_available(context, image, card):
                continue
            x, y, w, h = card
            if not click(context, x + w // 2, y + h // 2):
                return CustomAction.RunResult(success=False)
            image = wait_page(context, ["RaidTarget"], 8)
            if image is None or not act(context, "RealmRaidChallenge", image):
                return CustomAction.RunResult(success=False)
            result = battle(context, "Raid")
            if result == "exhausted":
                return CustomAction.RunResult(success=True)
            if result == "error":
                return CustomAction.RunResult(success=False)
            if result == "success":
                image = wait_page(context, ["Raid"])
                if image is None:
                    return CustomAction.RunResult(success=False)
                if not raid_card_broken(context, image, card):
                    logger.warning("目标未出现攻破标记，不计为成功")
                    continue
                completed += 1
                logger.info("突破完成：%d/%d", completed, limit)
                if completed >= limit:
                    break
        logger.info("突破本轮结束，成功 %d 次；不自动刷新对手", completed)
        return CustomAction.RunResult(success=True)


def claim_page(context, page, claim_node, scrolls=0):
    """Require the expected page, and two stable empty scans before moving on."""
    for scroll in range(scrolls + 1):
        empty = 0
        for _ in range(40):
            if context.tasker.stopping:
                return False
            image = screenshot(context)
            if image is None:
                return False
            if act(context, "DailyRewardPopup", image):
                empty = 0
                continue
            if not hit(context.run_recognition("Page" + page, image)):
                return False
            if act(context, claim_node, image):
                empty = 0
                continue
            empty += 1
            if empty >= 2:
                break
            if not pause(context, .8):
                return False
        else:
            logger.error("领奖按钮持续存在，停止重复点击")
            return False
        if scroll < scrolls and not swipe(context, (865, 486), (865, 262)):
            return False
    return True


@AgentServer.custom_action("ClaimDailyRewards")
class ClaimDailyRewards(CustomAction):
    def run(self, context, argv):
        success = (
            navigate(context, ["Mail"])
            and claim_page(context, "Mail", "DailyRewardsClaimAllMail")
            and navigate(context, ["QuestTasks"])
            and claim_page(context, "QuestTasks", "DailyRewardsClaim", scrolls=5)
            and navigate(context, ["Quests"])
            and claim_page(context, "Quests", "DailyRewardsClaimReward")
        )
        if success:
            logger.info("邮件与花合战可领取项处理完成，停留当前页面")
        return CustomAction.RunResult(success=success)
