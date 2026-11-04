import hashlib
import os
import stat
from pathlib import Path


MODEL_EXTENSIONS = {
    ".safetensors",
    ".sft",
    ".ckpt",
    ".pt",
    ".pt2",
    ".pth",
    ".bin",
    ".gguf",
    ".pkl",
    ".onnx",
    ".engine",
    ".tflite",
}
EXCLUDED_KINDS = {"custom_nodes", "configs", "datasets", "input", "output", "temp", "web", "user"}
EXCLUDED_DIRECTORIES = {".git", "__pycache__", ".cache"}


def path_key(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def identity(path):
    return hashlib.sha256(path_key(path).encode("utf-8")).hexdigest()


def within(path, root):
    return path == root or root in path.parents


def linked(path):
    if path.is_symlink():
        return True
    if os.name == "nt":
        try:
            return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        except FileNotFoundError:
            return False
    return False


def fingerprint(path):
    info = path.stat()
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def discover_roots(folder_paths):
    roots = []
    seen = set()
    warnings = []
    registry = dict(folder_paths.folder_names_and_paths)
    for kind, entry in registry.items():
        if kind in EXCLUDED_KINDS:
            continue
        try:
            paths = folder_paths.get_folder_paths(kind)
            extensions = MODEL_EXTENSIONS | {str(ext).lower() for ext in entry[1] if str(ext).startswith(".")}
            for directory in paths:
                root = Path(directory).expanduser().resolve()
                key = (kind, path_key(root))
                if key not in seen:
                    roots.append({"kind": kind, "path": root, "extensions": extensions})
                    seen.add(key)
        except (OSError, ValueError, TypeError) as error:
            warnings.append(f"Cannot resolve {kind}: {error}")
    # Include unregistered subdirectories in the main models tree, too.
    base = getattr(folder_paths, "models_dir", None)
    if base:
        try:
            roots.append({"kind": "other", "path": Path(base).resolve(), "extensions": MODEL_EXTENSIONS})
        except OSError as error:
            warnings.append(f"Cannot resolve the default models directory: {error}")
    return roots, warnings


def logical_path(path, root, kind):
    # Prefer the actual on-disk models/<...> hierarchy, including extra roots.
    for parent in path.parents:
        if parent.name.lower() == "models":
            return Path("models") / path.relative_to(parent)
    return Path("models") / kind / path.relative_to(root)


def scan(folder_paths, trash_root=None, excluded_roots=()):
    roots, warnings = discover_roots(folder_paths)
    excluded = list(excluded_roots) + ([trash_root] if trash_root else [])
    models = {}
    for root_info in roots:
        root, kind = root_info["path"], root_info["kind"]
        try:
            exists = root.is_dir()
        except OSError as error:
            warnings.append(f"Cannot access {root}: {error}")
            continue
        if not exists:
            warnings.append(f"Model directory is unavailable: {root}")
            continue

        def walk_error(error):
            warnings.append(f"Cannot scan {error.filename}: {error.strerror}")

        for directory, subdirs, files in os.walk(root, followlinks=False, onerror=walk_error):
            parent = Path(directory)
            kept = []
            for name in subdirs:
                child = parent / name
                try:
                    if name.lower() in EXCLUDED_DIRECTORIES or any(within(child.resolve(), root) for root in excluded):
                        continue
                    if linked(child):
                        warnings.append(
                            f"Skipped a linked subdirectory; register its target in ComfyUI to scan it: {child}"
                        )
                        continue
                    kept.append(name)
                except OSError as error:
                    warnings.append(f"Cannot access {child}: {error}")
            subdirs[:] = kept
            for filename in files:
                path = parent / filename
                disabled = filename.lower().endswith(".disabled")
                extension = Path(filename[:-9] if disabled else filename).suffix.lower()
                if extension not in root_info["extensions"]:
                    continue
                try:
                    if any(within(path.resolve(), root) for root in excluded):
                        continue
                    if not path.is_file():
                        continue
                    key = path_key(path)
                    if key in models:
                        if kind != "other":
                            if kind not in models[key]["kinds"]:
                                models[key]["kinds"].append(kind)
                            reference = {"kind": kind, "name": path.relative_to(root).as_posix()}
                            if reference not in models[key]["references"]:
                                models[key]["references"].append(reference)
                        continue
                    is_link = linked(path)
                    model = {
                        "id": identity(path),
                        "path": str(path),
                        "root": str(root),
                        "kind": kind,
                        "kinds": [kind],
                        "relative_path": path.relative_to(root).as_posix(),
                        "logical_path": logical_path(path, root, kind).as_posix(),
                        "size_bytes": path.stat().st_size,
                        "disabled": disabled,
                        "can_move": not is_link,
                        "move_reason": "Linked files cannot be moved; manage their targets instead." if is_link else "",
                        "references": [{"kind": kind, "name": path.relative_to(root).as_posix()}],
                        "fingerprint": fingerprint(path),
                    }
                    models[key] = model
                except (OSError, ValueError) as error:
                    warnings.append(f"Cannot inspect {path}: {error}")
    return (
        list(models.values()),
        [{"kind": item["kind"], "path": str(item["path"])} for item in roots],
        list(dict.fromkeys(warnings)),
    )
