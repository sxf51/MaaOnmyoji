"""Read-only page inspection using the shared Pipeline recognitions."""
from __future__ import annotations

# Specific overlays precede their underlying pages.
PAGES = (
    "NoResource", "Bounty", "Reward", "ExitExplore", "ExitTeam", "RaidTarget", "Mail",
    "TeamInvite", "TeamCreate", "TeamList", "TeamRoom", "QuestTasks", "Quests",
    "Raid", "ChapterField", "Chapter", "MitamaStage", "MitamaMenu",
    "AwakeningStage", "AwakeningMenu", "Explore", "Home", "BattleEntering", "Battle", "Result", "Loading",
)


def hit(detail):
    return detail is not None and bool(detail.hit) and detail.box is not None


def detect_page(context, image):
    for page in PAGES:
        if hit(context.run_recognition("Page" + page, image)):
            return page
    return "Unknown"


def screenshot(context):
    return context.tasker.controller.post_screencap().wait().get()
