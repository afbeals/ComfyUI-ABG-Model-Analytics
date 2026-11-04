import functools
import inspect
import logging
import os
from pathlib import Path

from .inventory import discover_roots, path_key, within

log = logging.getLogger("ABGModelAnalytics")
INPUT_KINDS = {
    "ckpt_name": "checkpoints",
    "checkpoint": "checkpoints",
    "lora_name": "loras",
    "vae_name": "vae",
    "unet_name": "diffusion_models",
    "clip_name": "text_encoders",
    "clip_name1": "text_encoders",
    "clip_name2": "text_encoders",
    "clip_name3": "text_encoders",
    "clip_name4": "text_encoders",
    "control_net_name": "controlnet",
    "clip_vision": "clip_vision",
    "style_model_name": "style_models",
    "upscale_model": "upscale_models",
}


def reachable_nodes(prompt, outputs):
    pending = list(outputs or prompt.keys())
    visited = set()
    while pending:
        key = str(pending.pop())
        if key in visited or key not in prompt:
            continue
        visited.add(key)
        node = prompt[key]
        yield node
        for value in node.get("inputs", {}).values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int):
                pending.append(value[0])


def referenced_paths(folder_paths, prompt, outputs):
    roots, _ = discover_roots(folder_paths)
    paths = set()
    for node in reachable_nodes(prompt, outputs):
        for name, value in node.get("inputs", {}).items():
            if not isinstance(value, str) or not value or value.lower().endswith(".disabled"):
                continue
            relative = Path(value.replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts:
                continue
            preferred = INPUT_KINDS.get(name)
            candidates = {}
            for item in roots:
                if item["kind"] == "other" or (preferred and item["kind"] != preferred):
                    continue
                if relative.suffix.lower() not in item["extensions"]:
                    continue
                # Use ComfyUI's own resolution order when roots contain duplicates.
                try:
                    result = folder_paths.get_full_path(item["kind"], os.fspath(relative))
                    if result:
                        resolved = Path(result).resolve()
                        if any(within(resolved, root["path"]) for root in roots):
                            candidates[path_key(resolved)] = resolved
                except (OSError, KeyError, ValueError):
                    continue
            # Ambiguous names across kinds provide no reliable file identity.
            if len(candidates) == 1:
                paths.update(candidates)
    return paths


def install_hooks(analytics):
    import comfy.utils as comfy_utils
    import execution

    if not getattr(comfy_utils.load_torch_file, "_abg_analytics", False):
        original = comfy_utils.load_torch_file

        @functools.wraps(original)
        def load(*args, **kwargs):
            result = original(*args, **kwargs)
            path = args[0] if args else kwargs.get("ckpt")
            if isinstance(path, (str, os.PathLike)):
                analytics.record_loaded(path)
            return result

        load._abg_analytics = True
        comfy_utils.load_torch_file = load

    executor = execution.PromptExecutor
    method = "execute_async" if hasattr(executor, "execute_async") else "execute"
    original_execute = getattr(executor, method)
    if getattr(original_execute, "_abg_analytics", False):
        return

    def start():
        # A cleanup operation holds this same lock until its file moves finish.
        with analytics.lock:
            analytics.active_executions += 1

    def finish(instance, prompt, outputs, completed):
        try:
            if completed and getattr(instance, "success", False):
                analytics.record_workflow(prompt, outputs)
        finally:
            with analytics.lock:
                analytics.active_executions -= 1

    if inspect.iscoroutinefunction(original_execute):

        @functools.wraps(original_execute)
        async def execute(instance, prompt, prompt_id, *args, **kwargs):
            start()
            completed = False
            try:
                result = await original_execute(instance, prompt, prompt_id, *args, **kwargs)
                completed = True
                return result
            finally:
                outputs = kwargs.get("execute_outputs", args[1] if len(args) > 1 else [])
                finish(instance, prompt, outputs, completed)
    else:

        @functools.wraps(original_execute)
        def execute(instance, prompt, prompt_id, *args, **kwargs):
            start()
            completed = False
            try:
                result = original_execute(instance, prompt, prompt_id, *args, **kwargs)
                completed = True
                return result
            finally:
                outputs = kwargs.get("execute_outputs", args[1] if len(args) > 1 else [])
                finish(instance, prompt, outputs, completed)

    execute._abg_analytics = True
    setattr(executor, method, execute)
    log.info("Tracking successful model loads and successful workflow references (%s).", method)
