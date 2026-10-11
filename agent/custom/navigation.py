"""Fresh-frame page detection and bounded navigation shared by all tasks.

Page probes and click targets live in Navigation.json. No page cache is kept:
popups, manual input and task transitions can invalidate the previous screen.
"""
from __future__ import annotations

import logging
import time
from collections import deque

logger = logging.getLogger("MaaOnmyoji.navigation")

# Specific overlays must precede their underlying pages.
PAGES = (
    "Reward", "NoResource", "ExitExplore", "ExitTeam", "RaidTarget", "Mail", "TeamInvite", "TeamCreate", "TeamList", "TeamRoom",
    "QuestTasks", "Quests", "Raid", "ChapterField", "Chapter",
    "MitamaStage", "MitamaMenu", "Explore", "Home", "Battle", "Result", "Loading",
)
EDGES = {
    "Home": [("Explore", "NavOpenExplore"), ("Quests", "NavOpenQuests")],
    "Explore": [("Raid", "NavOpenRaid"), ("MitamaMenu", "NavOpenMitama"),
                ("Home", "ReturnMainBackButton")],
    "MitamaMenu": [("MitamaStage", "NavOpenBaqi"), ("Explore", "ReturnMainBackButton")],
    "MitamaStage": [("Explore", "ReturnMainBackButton")],
    "Chapter": [("Explore", "ReturnMainBackButton")],
    "ChapterField": [("Chapter", "ReturnMainBackButton")],
    "Raid": [("Explore", "NavClose")],
    "RaidTarget": [("Raid", "NavDismissRaidTarget")],
    "Quests": [("Home", "NavBack"), ("QuestTasks", "NavQuestTasks")],
    "QuestTasks": [("Home", "NavBack"), ("Quests", "NavQuestRewards")],
    "TeamCreate": [("TeamList", "NavCancelCreate")],
    "TeamList": [("MitamaStage", "ReturnMainBackButton")],
    "TeamRoom": [("MitamaStage", "ReturnMainBackButton")],
    "TeamInvite": [("TeamRoom", "NavCancelInvite")],
}


def hit(detail):
    return detail is not None and bool(detail.hit) and detail.box is not None


def detect_page(context, image):
    for page in PAGES:
        if hit(context.run_recognition("Page" + page, image)):
            return page
    return "Unknown"


def first_edge(source, targets):
    queue = deque([(source, None)])
    visited = {source}
    while queue:
        page, first = queue.popleft()
        for dest, action in EDGES.get(page, []):
            edge = first or action
            if dest in targets:
                return edge
            if dest not in visited:
                visited.add(dest)
                queue.append((dest, edge))
    return None


def screenshot(context):
    return context.tasker.controller.post_screencap().wait().get()


def pause(context, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if context.tasker.stopping:
            return False
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    return True


def act(context, node, image):
    if context.tasker.stopping or image is None:
        return False
    detail = context.run_recognition(node, image)
    if not hit(detail):
        return False
    result = context.run_action(node, detail.box)
    return result is not None and result.success and pause(context, 1.2)


def navigate(context, targets, timeout=60, max_steps=24):
    if not targets or any(target not in PAGES for target in targets):
        logger.error("未知导航目标：%s", targets)
        return False
    deadline = time.monotonic() + timeout
    steps = 0
    last = None
    repeats = 0
    while time.monotonic() < deadline and steps < max_steps:
        if context.tasker.stopping:
            return False
        image = screenshot(context)
        if image is None:
            return False
        page = detect_page(context, image)
        if page != last:
            logger.info("当前页面：%s；目标：%s", page, "/".join(targets))
            last, repeats = page, 0
        if page in targets:
            return True
        if page in {"Battle", "Result"}:
            logger.error("战斗/结算中不执行页面退出，请先完成当前战斗")
            return False
        if page == "Loading":
            if not pause(context, 0.5):
                return False
            continue
        if repeats >= 4:
            logger.error("页面未变化，停止导航：%s", page)
            return False
        if page == "Reward":
            candidates = ["DailyRewardPopup"]
        elif page == "NoResource":
            candidates = ["NavCancelNoResource"]
        elif page == "ExitExplore":
            candidates = ["NavConfirmExitExplore"]
        elif page == "ExitTeam":
            candidates = ["NavConfirmExitTeam"]
        elif page == "Mail":
            candidates = ["NavClose"]
        elif "Mail" in targets and page in {"Home", "Explore", "MitamaStage", "MitamaMenu", "Chapter"}:
            candidates = ["NavOpenMail"]
        elif page == "Home" and any(target in targets for target in ("QuestTasks", "Quests")):
            candidates = ["NavOpenQuests", "NavExpandHomeMenu"]
        else:
            edge = first_edge(page, targets)
            candidates = [edge] if edge else ["NavClose", "NavBack", "ReturnMainHomeButton", "ExitXianTing"]
            # A dedicated return-to-home request prefers the Home icon.
            if targets == ["Home"]:
                candidates.insert(0, "ReturnMainHomeButton")
        if any(act(context, node, image) for node in candidates):
            steps += 1
            repeats += 1
        else:
            # Unknown screens are not clicked by coordinates or Android Back.
            repeats += 1
            if not pause(context, 0.6):
                return False
    logger.error("导航超时或超过步数：%s", targets)
    return False
