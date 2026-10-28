"""Same-frame target recognition and local touch-area validation."""
from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition

from custom.action.target_count import _parse_params
from custom.input_safety import action_region, regions_safe, stop_input
from custom.page_state import hit


@AgentServer.custom_recognition("InputTarget")
class InputTarget(CustomRecognition):
    """Return the original target box only if the actual click range is safe."""

    def analyze(self, context, argv):
        try:
            params = _parse_params(argv.custom_recognition_param)
            public = params["target_node"]
            detail = context.run_recognition(public, argv.image)
            if not hit(detail):
                return None
            node = context.get_node_data(argv.node_name)
            action = node.get("action", "Click")
            custom = action.get("param", {}).get("custom_action") if isinstance(action, dict) else None
            # Bezier checks its freshly generated full track immediately before touch down.
            if custom != "BezierSwipe":
                region = action_region(node, detail.box)
                if not regions_safe(context, argv.image, [region], public):
                    return None
            return CustomRecognition.AnalyzeResult(box=list(detail.box), detail={"target_node": public})
        except (KeyError, AttributeError, RuntimeError, TypeError, ValueError) as exc:
            stop_input(context, f"输入目标参数无效：{exc}")
            return None
