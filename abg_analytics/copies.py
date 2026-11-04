"""Separate name collisions from content-verified duplicate files."""

import hashlib
import os
from collections import defaultdict
from pathlib import Path

from .inventory import fingerprint, linked, path_key


def annotate_copies(folder_paths, models, hashes):
    by_reference = defaultdict(dict)
    by_hash = defaultdict(list)
    checked = set()
    for model in models:
        model.update(copies=[], duplicate_paths=[])
        for reference in model["references"]:
            if reference["kind"] != "other" and not model["disabled"]:
                key = (reference["kind"], os.path.normcase(reference["name"]))
                by_reference[key][model["id"]] = model
        cached = hashes.get(path_key(model["path"]))
        if cached and cached["fingerprint"] == model["fingerprint"]:
            by_hash[cached["sha256"]].append(model)
            checked.add(model["id"])
    name_groups = 0
    for (kind, name), items in by_reference.items():
        group = list(items.values())
        if len(group) < 2:
            continue
        name_groups += 1
        try:
            active = folder_paths.get_full_path(kind, name)
            active_key = path_key(Path(active).resolve()) if active else None
        except (OSError, ValueError, KeyError):
            active_key = None
        for model in group:
            model["copies"].append(
                {
                    "kind": kind,
                    "name": name,
                    "role": "active"
                    if path_key(Path(model["path"]).resolve()) == active_key
                    else "shadowed"
                    if active_key
                    else "unresolved",
                    "active_path": active,
                    "other_paths": [item["path"] for item in group if item["id"] != model["id"]],
                }
            )
    verified_groups = 0
    for group in by_hash.values():
        if len(group) > 1:
            verified_groups += 1
            for model in group:
                model["duplicate_paths"] = [other["path"] for other in group if other["id"] != model["id"]]
    for model in models:
        model["shadowed"] = bool(model["copies"]) and all(copy["role"] == "shadowed" for copy in model["copies"])
        model["hash_checked"] = model["id"] in checked
    return {"same_name_groups": name_groups, "verified_groups": verified_groups, "checked_files": len(checked)}


def check_duplicates(models, store):
    groups = defaultdict(list)
    for model in models:
        groups[model["size_bytes"]].append(model)
    cached = store.hashes()
    result = {"hashed_files": 0, "cached_files": 0, "warnings": []}
    for group in groups.values():
        if len(group) < 2:
            continue
        for model in group:
            path = Path(model["path"])
            key = path_key(path)
            entry = cached.get(key)
            if entry and entry["fingerprint"] == model["fingerprint"]:
                result["cached_files"] += 1
                continue
            try:
                if linked(path):
                    continue
                digest = hashlib.sha256()
                if fingerprint(path) != model["fingerprint"]:
                    raise ValueError("File changed before hashing")
                with path.open("rb") as reader:
                    while chunk := reader.read(8 * 1024 * 1024):
                        digest.update(chunk)
                if fingerprint(path) != model["fingerprint"]:
                    raise ValueError("File changed during hashing")
                store.save_hash(key, model["fingerprint"], digest.hexdigest())
                result["hashed_files"] += 1
            except (OSError, ValueError) as error:
                result["warnings"].append(f"Could not check {path}: {error}")
    return result
