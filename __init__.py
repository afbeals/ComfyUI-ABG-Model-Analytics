"""ComfyUI ABG Model Analytics — personal inventory and reversible cleanup.

Inspired by Bennowan's ComfyUI-Model-Analytics; see LICENSE and NOTICE.
"""

import logging

import folder_paths
from server import PromptServer

from .abg_analytics.api import register_routes
from .abg_analytics.service import Analytics
from .abg_analytics.tracker import install_hooks

server = PromptServer.instance


def queue_idle():
    running, pending = server.prompt_queue.get_current_queue()
    return not running and not pending


analytics = Analytics(folder_paths, queue_idle=queue_idle)
register_routes(server.routes, analytics)
try:
    install_hooks(analytics)
except Exception:
    logging.getLogger("ABGModelAnalytics").exception(
        "Usage hooks unavailable. Inventory and trash remain available; usage may be incomplete."
    )

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
WEB_DIRECTORY = "./web"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
