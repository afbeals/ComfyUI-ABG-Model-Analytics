"""Find saved-workflow references without treating a saved file as recent usage."""

import json
import os
from collections import defaultdict
from pathlib import Path

from .inventory import linked, path_key
from .tracker import INPUT_KINDS

NODE_KINDS = {
    "CheckpointLoader": "checkpoints",
    "CheckpointLoaderSimple": "checkpoints",
    "LoraLoader": "loras",
    "LoraLoaderModelOnly": "loras",
    "VAELoader": "vae",
    "UNETLoader": "diffusion_models",
    "UnetLoaderGGUF": "diffusion_models",
    "CLIPLoader": "text_encoders",
    "DualCLIPLoader": "text_encoders",
    "TripleCLIPLoader": "text_encoders",
    "CLIPVisionLoader": "clip_vision",
    "ControlNetLoader": "controlnet",
    "DiffControlNetLoader": "controlnet",
    "UpscaleModelLoader": "upscale_models",
}
MAX_WORKFLOW_BYTES = 32 * 1024 * 1024


def normalized(value):
    return os.path.normcase(value.replace("\\", "/"))


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def node_references(value):
    if isinstance(value, dict):
        node_type = value.get("class_type", value.get("type"))
        node_kind = NODE_KINDS.get(node_type) if isinstance(node_type, str) else None
        inputs = value.get("inputs")
        if isinstance(inputs, dict):
            for name, contents in inputs.items():
                for text in strings(contents):
                    yield text, INPUT_KINDS.get(name, node_kind)
        widgets = value.get("widgets_values")
        if isinstance(widgets, dict):
            for name, contents in widgets.items():
                for text in strings(contents):
                    yield text, INPUT_KINDS.get(name, node_kind)
        elif isinstance(widgets, list):
            for text in strings(widgets):
                yield text, node_kind
        for key, child in value.items():
            if key not in {"inputs", "widgets_values"}:
                yield from node_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from node_references(child)


def scan_workflows(folder_paths, models):
    warnings = []
    result = {"workflows": [], "directories": [], "warnings": warnings}
    for model in models:
        model["workflows"] = []
    try:
        user = Path(folder_paths.get_user_directory()).resolve()
        candidates = [user / "workflows", user / "default" / "workflows"]
        if user.is_dir():
            candidates.extend(child / "workflows" for child in user.iterdir() if child.is_dir() and not linked(child))
    except OSError as error:
        warnings.append(f"Cannot discover saved workflows: {error}")
        return result
    index = defaultdict(dict)
    logical_index = defaultdict(dict)
    absolute_index = {}
    by_id = {model["id"]: model for model in models}
    for model in models:
        absolute_index[path_key(model["path"])] = model
        logical_index[normalized(model["logical_path"])][model["id"]] = model
        for reference in model["references"]:
            if reference["kind"] != "other":
                index[normalized(reference["name"])][(model["id"], reference["kind"])] = model
    directories = sorted(set(candidates), key=str)
    for directory in directories:
        try:
            if linked(directory) or not directory.is_dir():
                continue
        except OSError as error:
            warnings.append(f"Cannot inspect workflow directory {directory}: {error}")
            continue
        result["directories"].append(str(directory))

        def on_error(error):
            warnings.append(f"Cannot scan workflows {error.filename}: {error.strerror}")

        for parent, subdirs, files in os.walk(directory, followlinks=False, onerror=on_error):
            safe_subdirs = []
            for name in subdirs:
                try:
                    if not linked(Path(parent) / name):
                        safe_subdirs.append(name)
                except OSError as error:
                    warnings.append(f"Cannot inspect workflow subdirectory: {error}")
            subdirs[:] = sorted(safe_subdirs)
            for name in sorted(files):
                if not name.lower().endswith(".json"):
                    continue
                path = Path(parent) / name
                try:
                    if linked(path):
                        continue
                    with path.open("rb") as reader:
                        raw = reader.read(MAX_WORKFLOW_BYTES + 1)
                    if len(raw) > MAX_WORKFLOW_BYTES:
                        raise ValueError("Workflow exceeds the 32 MiB scan limit")
                    data = json.loads(raw.decode("utf-8-sig"))
                    matches = {}
                    for text, preferred in node_references(data):
                        candidate = Path(text.replace("\\", "/"))
                        if ".." in candidate.parts:
                            continue
                        direct = absolute_index.get(path_key(candidate)) if candidate.is_absolute() else None
                        if direct:
                            matches.setdefault(direct["id"], "possible")
                        key = normalized(text)
                        pairs = list(index.get(key, {}).items())
                        if preferred:
                            pairs = [(item, model) for item, model in pairs if item[1] == preferred]
                        for (model_id, kind), model in pairs:
                            match = "possible"
                            if preferred:
                                resolved = folder_paths.get_full_path(kind, text.replace("\\", os.sep))
                                if resolved:
                                    match = (
                                        "resolved"
                                        if path_key(Path(resolved).resolve()) == path_key(model["path"])
                                        else "fallback"
                                    )
                            rank = {"resolved": 3, "possible": 2, "fallback": 1}
                            if rank[match] > rank.get(matches.get(model_id), 0):
                                matches[model_id] = match
                        if not pairs:
                            for model_id in logical_index.get(key, {}):
                                matches.setdefault(model_id, "possible")
                    workflow = {
                        "name": path.relative_to(user).as_posix(),
                        "path": str(path),
                        "models": [{"id": model_id, "match": match} for model_id, match in matches.items()],
                    }
                    result["workflows"].append(workflow)
                    for model_id, match in matches.items():
                        by_id[model_id]["workflows"].append(
                            {"name": workflow["name"], "path": str(path), "match": match}
                        )
                except (OSError, ValueError, UnicodeError, RecursionError, KeyError) as error:
                    warnings.append(f"Cannot read workflow {path}: {error}")
    result["workflows"].sort(key=lambda item: item["name"].lower())
    for model in models:
        model["workflows"].sort(key=lambda reference: reference["name"])
    return result
