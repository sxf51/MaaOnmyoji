"""Data-only dispatch policy; image observations and inputs always keep guards."""
from __future__ import annotations

from typing import Any, Callable

GUARD_PREFIX = ["[JumpBack]CommonBountyInterrupt"]
_GUARDS = set(GUARD_PREFIX)
_CONTROL_ACTIONS = {"ResetCount", "NodeOverride", "ConfigureTeamRecognition", "TargetCountDetermine", "TargetCountInit"}


def is_control_node(node: Any) -> bool:
    """Only known no-image bookkeeping with a continuation may run without OCR."""
    if not isinstance(node, dict) or not node.get("next") or node.get("inverse") or not node.get("enabled", True):
        return False
    if node.get("max_hit", 2**32-1) < 2**32-1:
        return False
    for field in ("pre_wait_freezes", "post_wait_freezes"):
        wait = node.get(field)
        if (isinstance(wait, dict) and wait.get("time", 0)) or (isinstance(wait, (int, float)) and wait):
            return False
    recognition = node.get("recognition", "DirectHit")
    kind = recognition.get("type") if isinstance(recognition, dict) else recognition
    if kind != "DirectHit":
        return False
    action = node.get("action", "DoNothing")
    if isinstance(action, str):
        return action == "DoNothing"
    if not isinstance(action, dict):
        return False
    return action.get("type") == "DoNothing" or (action.get("type") == "Custom" and action.get("param", {}).get("custom_action") in _CONTROL_ACTIONS)


def is_control_route(routes: list, lookup: Callable[[str], Any]) -> bool:
    """A sole ordinary control destination; mixed, missing and jump routes stay guarded."""
    body = [node for node in routes if not isinstance(node, str) or node not in _GUARDS]
    if len(body) != 1 or not isinstance(body[0], str) or body[0].startswith("["):
        return False
    try:
        name, visited = body[0], set()
        while True:
            if name in visited:
                return False
            visited.add(name)
            node = lookup(name)
            if not isinstance(node, dict):
                return False
            if not is_control_node(node):
                return len(visited) > 1
            following = [item for item in node["next"] if not isinstance(item, str) or item not in _GUARDS]
            if len(following) != 1 or not isinstance(following[0], str) or following[0].startswith("["):
                return True
            name = following[0]
    except (KeyError, RuntimeError, TypeError, ValueError):
        return False


def with_purchase_stop(nodes: list, context=None) -> list:
    """Add cooperative interruption before visual candidates; inputs guard their own area."""
    body = [node for node in nodes if not isinstance(node, str) or node not in _GUARDS]
    if context is not None and is_control_route(body, context.get_node_data):
        return body
    return [*GUARD_PREFIX, *body]
