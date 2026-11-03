import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .copies import annotate_copies, check_duplicates
from .inventory import discover_roots, fingerprint, path_key, scan, within
from .moving import check_plain_path, move_no_replace, unique_destination, validate_trash_directory
from .store import Store
from .trash import summarize_trash
from .workflows import scan_workflows

log = logging.getLogger("ABGModelAnalytics")


class Analytics:
    def __init__(self, folder_paths, data_directory=None, queue_idle=None):
        self.folder_paths = folder_paths
        directory = data_directory or Path(folder_paths.get_user_directory()) / "abg-model-analytics"
        self.store = Store(directory)
        self.lock = threading.RLock()
        self.active_executions = 0
        self.queue_idle = queue_idle or (lambda: True)
        self.plans = {}
        self.hash_lock = threading.Lock()

    def _scan(self, trash_root=None):
        excluded = [Path(location) for location in self.store.trash_locations()]
        models, roots, warnings = scan(self.folder_paths, trash_root, excluded_roots=excluded)
        protected = self.store.protected_paths()
        for model in models:
            model["protected"] = path_key(model["path"]) in protected
            if model["protected"]:
                model["can_move"] = False
                model["move_reason"] = "Protected model. Remove protection before moving it."
        return models, roots, warnings

    def protect(self, model_id, protected):
        if not isinstance(model_id, str) or not isinstance(protected, bool):
            raise ValueError("Specify a model ID and a boolean protected value.")
        with self.lock:
            models, _, _ = self._scan()
            model = next((item for item in models if item["id"] == model_id), None)
            if not model:
                raise ValueError("Model no longer exists. Refresh the inventory.")
            self.store.protect(path_key(model["path"]), protected)
            self.plans.clear()
            return {"model_id": model_id, "protected": protected}

    def find_duplicates(self):
        if not self.hash_lock.acquire(blocking=False):
            raise ValueError("An exact duplicate check is already running.")
        try:
            models, _, _ = self._scan()
            result = check_duplicates(models, self.store)
            result.update(annotate_copies(self.folder_paths, models, self.store.hashes()))
            return result
        finally:
            self.hash_lock.release()

    def trash_summary(self):
        directory = self.store.settings()["trash_directory"]
        current = str(Path(directory) / "trash") if directory else None
        return summarize_trash(self.store.trash_locations(), current)

    def configure(self, values):
        if not isinstance(values, dict):
            raise ValueError("Settings must be a JSON object.")
        with self.lock:
            settings = self.store.settings()
            roots, _ = discover_roots(self.folder_paths)
            directory = validate_trash_directory(values.get("trash_directory", settings["trash_directory"]), roots)
            result = self.store.configure(directory, values.get("stale_days", settings["stale_days"]))
            self.plans.clear()
            return result

    def inventory(self):
        settings = self.store.settings()
        trash_root = Path(settings["trash_directory"]) / "trash" if settings["trash_directory"] else None
        models, roots, warnings = self._scan(trash_root)
        copies = annotate_copies(self.folder_paths, models, self.store.hashes())
        workflows = scan_workflows(self.folder_paths, models)
        warnings.extend(workflows.pop("warnings"))
        history = self.store.history()
        timestamp = datetime.now(timezone.utc)
        for model in models:
            entry = history.get(path_key(model["path"]), {})
            if entry.get("signature") != model["fingerprint"][2:]:
                entry = {}  # A replacement at the same path has no observed usage yet.
            model.update({key: entry.get(key) for key in ("last_used", "last_loaded", "last_referenced")})
            model["use_count"] = entry.get("use_count", 0)
            last = model["last_used"]
            model["status"] = (
                "unknown"
                if not last
                else (
                    "recent"
                    if (timestamp - datetime.fromisoformat(last)).total_seconds() <= settings["stale_days"] * 86400
                    else "stale"
                )
            )
            model.pop("fingerprint")
            model.pop("references")
        return {
            "models": models,
            "roots": roots,
            "warnings": warnings,
            "settings": settings,
            "workflow_scan": workflows,
            "copies": copies,
            "trash": self.trash_summary(),
        }

    def _ensure_idle(self):
        if self.active_executions or not self.queue_idle():
            raise ValueError("Wait until ComfyUI has no running or queued generations before moving files.")

    def preview(self, model_ids):
        if (
            not isinstance(model_ids, list)
            or not model_ids
            or len(model_ids) > 10000
            or any(not isinstance(value, str) for value in model_ids)
        ):
            raise ValueError("Select between 1 and 10000 model IDs.")
        with self.lock:
            self._ensure_idle()
            settings = self.store.settings()
            roots, _ = discover_roots(self.folder_paths)
            directory = validate_trash_directory(settings["trash_directory"], roots)
            if not directory:
                raise ValueError("Configure the trash directory first.")
            trash = Path(directory) / "trash"
            models, _, _ = self._scan(trash)
            scan_workflows(self.folder_paths, models)
            annotate_copies(self.folder_paths, models, self.store.hashes())
            by_id = {model["id"]: model for model in models}
            rows, reserved = [], set()
            for model_id in dict.fromkeys(model_ids):
                model = by_id.get(model_id)
                if not model:
                    raise ValueError("A selected model no longer exists. Refresh the inventory.")
                if not model["can_move"]:
                    raise ValueError(model["move_reason"])
                source = Path(model["path"])
                check_plain_path(source)
                target = unique_destination(trash, model["logical_path"], model_id, reserved)
                rows.append(
                    {
                        "id": model_id,
                        "source": str(source),
                        "destination": str(target),
                        "size_bytes": model["size_bytes"],
                        "fingerprint": model["fingerprint"],
                        "workflows": model["workflows"],
                        "copies": model["copies"],
                    }
                )
            plan_id = str(uuid.uuid4())
            # Expire old previews and bound memory if clients repeatedly preview.
            self.plans = {key: value for key, value in self.plans.items() if value["expires"] > time.monotonic()}
            if len(self.plans) >= 32:
                self.plans.pop(next(iter(self.plans)))
            self.plans[plan_id] = {"rows": rows, "directory": directory, "expires": time.monotonic() + 600}
            return {
                "plan_id": plan_id,
                "files": [{key: value for key, value in row.items() if key != "fingerprint"} for row in rows],
            }

    def trash(self, plan_id):
        with self.lock:
            self._ensure_idle()
            if not isinstance(plan_id, str):
                raise ValueError("A preview ID is required.")
            plan = self.plans.pop(plan_id, None)
            if not plan or plan["expires"] < time.monotonic():
                raise ValueError("This preview expired or was already used. Preview the selection again.")
            settings = self.store.settings()
            roots, _ = discover_roots(self.folder_paths)
            directory = validate_trash_directory(settings["trash_directory"], roots)
            if directory != plan["directory"]:
                raise ValueError("Trash settings changed; preview again.")
            current_models, _, _ = self._scan(Path(directory) / "trash")
            scan_workflows(self.folder_paths, current_models)
            allowed = {model["id"]: model for model in current_models}
            # Validate the entire batch before changing any files.
            for row in plan["rows"]:
                model = allowed.get(row["id"])
                if not model or not model["can_move"] or model["fingerprint"] != row["fingerprint"]:
                    raise ValueError("A selected model changed; refresh and preview again.")
                if model["workflows"] != row["workflows"]:
                    raise ValueError("Saved workflow references changed; preview the selection again.")
            results = []
            for row in plan["rows"]:
                move_id = str(uuid.uuid4())
                self.store.journal(move_id, row["source"], row["destination"], row["size_bytes"])
                try:
                    move_no_replace(row["source"], row["destination"], row["fingerprint"])
                    self.store.update_move(move_id, "trashed", fingerprint=fingerprint(Path(row["destination"])))
                    results.append(
                        {"id": move_id, "source": row["source"], "destination": row["destination"], "success": True}
                    )
                except (OSError, ValueError) as error:
                    self.store.update_move(move_id, "failed", str(error))
                    results.append({"id": move_id, "source": row["source"], "success": False, "error": str(error)})
            return {"results": results}

    def restore(self, move_id):
        with self.lock:
            self._ensure_idle()
            move = next((row for row in self.store.moves() if row["id"] == move_id), None)
            if not move or move["status"] != "trashed":
                raise ValueError("Only a completed trash move can be restored.")
            source, destination = Path(move["destination"]), Path(move["source"])
            # Journal paths are server-created, and retained even if settings change.
            check_plain_path(source)
            check_plain_path(destination)
            expected = fingerprint(source)
            if (move["fingerprint"] and expected != move["fingerprint"]) or expected[2] != move["size_bytes"]:
                raise ValueError("The trashed file changed; restore it manually after reviewing it.")
            self.store.update_move(move_id, "restoring")
            try:
                move_no_replace(source, destination, expected)
            except (OSError, ValueError) as error:
                self.store.update_move(move_id, "trashed", str(error))
                raise
            self.store.update_move(move_id, "restored")
            return {"source": str(source), "destination": str(destination), "success": True}

    def record_loaded(self, path):
        try:
            path = Path(path).resolve()
            roots, _ = discover_roots(self.folder_paths)
            if any(within(path, item["path"]) for item in roots):
                self.store.record([path_key(path)], "load")
        except Exception:
            log.exception("Could not record a model load; generation continues normally.")

    def record_workflow(self, prompt, outputs):
        """Refresh exact model references in a successful workflow, including cache hits.

        This is evidence of a successful workflow reference, not a claim that
        a weight file was reread or that every lazy branch was evaluated.
        """
        from .tracker import referenced_paths

        try:
            self.store.record(referenced_paths(self.folder_paths, prompt, outputs), "workflow")
        except Exception:
            log.exception("Could not record workflow references; generation continues normally.")
