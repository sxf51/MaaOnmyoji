"""Same-frame target recognition and local touch-area validation."""
from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition

from custom.action.target_count import _parse_params
from custom.input_safety import action_region, regions_safe, stop_input
from custom.page_state import hit


def gated_box(node, argv, public):
    """Reuse the native gate's same-frame target box passed in as roi, if wired."""
    recognition = node.get("recognition")
    if not isinstance(recognition, dict) or recognition.get("type") != "And":
        return None
    items = recognition.get("param", {}).get("all_of", [])
    guard = next((item for item in items if isinstance(item, dict)
                  and item.get("sub_name") == argv.node_name), None)
    if not items or items[0] != public or guard is None:
        return None
    param = guard.get("recognition", {}).get("param", {})
    if param.get("roi") != public or any(param.get("roi_offset", [0, 0, 0, 0])):
        return None
    box = list(argv.roi)
    return box if len(box) == 4 and box[2] > 0 and box[3] > 0 else None


@AgentServer.custom_recognition("InputTarget")
class InputTarget(CustomRecognition):
    """Return the original target box only if the actual click range is safe."""

    def analyze(self, context, argv):
        try:
            params = _parse_params(argv.custom_recognition_param)
            public = params["target_node"]
            node = context.get_node_data(argv.node_name)
            box = gated_box(node, argv, public)
            if box is None:
                detail = context.run_recognition(public, argv.image)
                if not hit(detail):
                    return None
                box = list(detail.box)
            action = node.get("action", "Click")
            custom = action.get("param", {}).get("custom_action") if isinstance(action, dict) else None
            # Bezier checks its freshly generated full track immediately before touch down.
            if custom != "BezierSwipe":
                region = action_region(node, box)
                if not regions_safe(context, argv.image, [region], public):
                    return None
            return CustomRecognition.AnalyzeResult(box=box, detail={"target_node": public})
        except (KeyError, AttributeError, RuntimeError, TypeError, ValueError) as exc:
            stop_input(context, f"输入目标参数无效：{exc}")
            return None
