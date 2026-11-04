import asyncio
import errno
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from abg_analytics.api import PREFIX, register_routes
from abg_analytics.inventory import path_key
from abg_analytics.service import Analytics
from abg_analytics.tracker import install_hooks, referenced_paths


class Folders:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.models_dir = str(self.directory / "comfy" / "models")
        self.folder_names_and_paths = {
            "loras": (
                [str(Path(self.models_dir) / "loras"), str(self.directory / "external" / "models" / "loras")],
                {".safetensors"},
            ),
            "checkpoints": ([str(Path(self.models_dir) / "checkpoints")], {".safetensors"}),
            "custom_nodes": ([str(self.directory / "custom_nodes")], set()),
        }

    def get_folder_paths(self, kind):
        return self.folder_names_and_paths[kind][0]

    def get_user_directory(self):
        return str(self.directory / "user")

    def get_full_path(self, kind, filename):
        for root in self.get_folder_paths(kind):
            path = Path(root) / filename
            if path.is_file():
                return str(path)


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.folders = Folders(self.base)
        self.analytics = Analytics(self.folders)
        self.local = self.make_file(
            Path(self.folders.models_dir) / "loras" / "flux2" / "same.safetensors", b"local weights"
        )
        self.external = self.make_file(
            self.base / "external" / "models" / "loras" / "flux2" / "same.safetensors", b"external weights"
        )
        self.analytics.configure({"trash_directory": str(self.base / "cleanup"), "stale_days": 30})

    def tearDown(self):
        self.temporary.cleanup()

    def make_file(self, path, content=b"weights"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def models(self):
        return self.analytics.inventory()["models"]

    def test_inventory_includes_external_and_unregistered_and_deduplicates_aliases(self):
        extra = self.make_file(Path(self.folders.models_dir) / "new_type" / "123456.bin")
        self.make_file(self.base / "custom_nodes" / "bundled.safetensors")
        self.folders.folder_names_and_paths["lora_alias"] = self.folders.folder_names_and_paths["loras"]
        models = self.models()
        self.assertEqual({item["path"] for item in models}, {str(self.local), str(self.external), str(extra)})
        self.assertEqual(len({item["id"] for item in models}), 3)
        self.assertTrue(all(item["status"] == "unknown" for item in models))

    def test_same_named_models_have_separate_history_and_recency(self):
        self.analytics.record_loaded(self.local)
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        with self.analytics.store.connection() as db:
            db.execute("UPDATE usage SET last_used=?", (old,))
        statuses = {item["path"]: item["status"] for item in self.models()}
        self.assertEqual(statuses[str(self.local)], "stale")
        self.assertEqual(statuses[str(self.external)], "unknown")
        self.analytics.configure({"stale_days": 90})
        self.assertEqual(next(item for item in self.models() if item["path"] == str(self.local))["status"], "recent")

    def test_settings_and_usage_survive_restart(self):
        self.analytics.record_loaded(self.local)
        restored = Analytics(self.folders)
        self.assertEqual(restored.store.settings()["trash_directory"], str(self.base / "cleanup"))
        self.assertIn(path_key(self.local), restored.store.history())

    def test_replaced_file_does_not_inherit_usage_at_the_same_path(self):
        self.analytics.record_loaded(self.local)
        self.local.write_bytes(b"a replacement model at the same filename")
        model = next(item for item in self.models() if item["path"] == str(self.local))
        self.assertEqual(model["status"], "unknown")
        self.assertIsNone(model["last_loaded"])
        self.analytics.record_loaded(self.local)
        model = next(item for item in self.models() if item["path"] == str(self.local))
        self.assertEqual(model["status"], "recent")
        self.assertEqual(model["use_count"], 1)

    def test_changed_trash_file_is_not_silently_restored(self):
        plan = self.analytics.preview([self.models()[0]["id"]])
        result = self.analytics.trash(plan["plan_id"])["results"][0]
        target = Path(result["destination"])
        original_size = target.stat().st_size
        target.unlink()
        target.write_bytes(b"x" * original_size)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.analytics.restore(result["id"])
        self.assertTrue(target.exists())

    def test_source_without_models_ancestor_gets_registered_category(self):
        outside = self.make_file(self.base / "my-loras" / "flux2" / "external.gguf")
        self.folders.folder_names_and_paths["loras"][0].append(str(self.base / "my-loras"))
        model = next(item for item in self.models() if item["path"] == str(outside))
        self.assertEqual(model["logical_path"], "models/loras/flux2/external.gguf")
        plan = self.analytics.preview([model["id"]])
        self.assertEqual(
            Path(plan["files"][0]["destination"]).relative_to(self.base / "cleanup" / "trash").as_posix(),
            "models/loras/flux2/external.gguf",
        )

    def test_case_only_destination_collision_is_separated_in_preview(self):
        self.external.unlink()
        external = self.make_file(self.external.with_name(self.external.name.upper()))
        self.assertTrue(external.exists())
        plan = self.analytics.preview([item["id"] for item in self.models()])
        destinations = [row["destination"].casefold() for row in plan["files"]]
        self.assertEqual(len(destinations), len(set(destinations)))

    def test_trash_preserves_hierarchy_and_restores(self):
        model = next(item for item in self.models() if item["path"] == str(self.local))
        plan = self.analytics.preview([model["id"]])
        target = self.base / "cleanup" / "trash" / "models" / "loras" / "flux2" / self.local.name
        self.assertEqual(plan["files"][0]["destination"], str(target))
        self.assertTrue(self.local.exists())  # Preview never changes model files.
        result = self.analytics.trash(plan["plan_id"])["results"][0]
        self.assertTrue(result["success"])
        self.assertFalse(self.local.exists())
        self.assertEqual(target.read_bytes(), b"local weights")
        self.analytics.restore(result["id"])
        self.assertEqual(self.local.read_bytes(), b"local weights")
        self.assertFalse(target.exists())
        self.assertEqual(self.analytics.store.moves()[0]["status"], "restored")

    def test_colliding_sources_and_existing_trash_files_never_overwrite(self):
        occupied = self.make_file(
            self.base / "cleanup" / "trash" / "models" / "loras" / "flux2" / self.local.name, b"keep me"
        )
        plan = self.analytics.preview([item["id"] for item in self.models()])
        destinations = [row["destination"] for row in plan["files"]]
        self.assertEqual(len(destinations), len(set(destinations)))
        results = self.analytics.trash(plan["plan_id"])["results"]
        self.assertTrue(all(result["success"] for result in results))
        self.assertEqual(occupied.read_bytes(), b"keep me")
        self.assertEqual({Path(path).read_bytes() for path in destinations}, {b"local weights", b"external weights"})

    def test_destination_created_after_preview_does_not_overwrite_or_remove_source(self):
        plan = self.analytics.preview([self.models()[0]["id"]])
        target = self.make_file(Path(plan["files"][0]["destination"]), b"new occupant")
        result = self.analytics.trash(plan["plan_id"])["results"][0]
        self.assertFalse(result["success"])
        self.assertEqual(target.read_bytes(), b"new occupant")
        self.assertTrue(Path(plan["files"][0]["source"]).exists())

    def test_restore_never_overwrites_new_original_and_keeps_trash(self):
        plan = self.analytics.preview([self.models()[0]["id"]])
        result = self.analytics.trash(plan["plan_id"])["results"][0]
        original = self.make_file(Path(result["source"]), b"replacement")
        with self.assertRaises(FileExistsError):
            self.analytics.restore(result["id"])
        self.assertEqual(original.read_bytes(), b"replacement")
        self.assertTrue(Path(result["destination"]).exists())
        self.assertEqual(self.analytics.store.moves()[0]["status"], "trashed")

    def test_changed_source_invalidates_whole_preview_before_any_move(self):
        plan = self.analytics.preview([item["id"] for item in self.models()])
        self.local.write_bytes(b"new data written since preview")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.analytics.trash(plan["plan_id"])
        self.assertTrue(self.local.exists())
        self.assertTrue(self.external.exists())

    def test_settings_change_invalidates_preview_and_empty_setting_disables_moves(self):
        plan = self.analytics.preview([self.models()[0]["id"]])
        self.analytics.configure({"trash_directory": ""})
        with self.assertRaises(ValueError):
            self.analytics.trash(plan["plan_id"])
        with self.assertRaisesRegex(ValueError, "Configure"):
            self.analytics.preview([self.models()[0]["id"]])

    def test_invalid_settings_and_trash_inside_model_tree_are_rejected(self):
        for value in [-1, 0, float("nan"), float("inf"), "30", True]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.analytics.configure({"stale_days": value})
        for path in ["relative/path", self.folders.models_dir, str(self.local.parent), str(self.external.parent)]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.analytics.configure({"trash_directory": path})

    def test_registered_root_changes_invalidate_destination_safety(self):
        plan = self.analytics.preview([self.models()[0]["id"]])
        self.folders.folder_names_and_paths["new_models"] = ([str(self.base / "cleanup")], {".safetensors"})
        with self.assertRaisesRegex(ValueError, "outside"):
            self.analytics.trash(plan["plan_id"])
        self.assertTrue(self.local.exists())

    def test_unknown_model_id_or_path_is_not_accepted(self):
        with self.assertRaises(ValueError):
            self.analytics.preview([str(self.base / "not-a-model.txt")])
        with self.assertRaises(ValueError):
            self.analytics.preview([])

    def test_no_moves_during_running_or_queued_generation(self):
        self.analytics.queue_idle = lambda: False
        with self.assertRaisesRegex(ValueError, "queued"):
            self.analytics.preview([self.models()[0]["id"]])
        self.analytics.queue_idle = lambda: True
        self.analytics.active_executions = 1
        with self.assertRaisesRegex(ValueError, "running"):
            self.analytics.preview([self.models()[0]["id"]])

    @unittest.skipIf(os.name == "nt", "Creating symlinks requires Windows developer mode or elevation.")
    def test_linked_files_visible_but_not_movable_and_subdirectory_links_are_skipped(self):
        link = self.local.parent / "linked.safetensors"
        link.symlink_to(self.external)
        directory_link = self.local.parent / "linked-directory"
        directory_link.symlink_to(self.external.parent, target_is_directory=True)
        data = self.analytics.inventory()
        model = next(item for item in data["models"] if item["path"] == str(link))
        self.assertFalse(model["can_move"])
        self.assertTrue(any("linked subdirectory" in warning for warning in data["warnings"]))
        with self.assertRaisesRegex(ValueError, "Linked"):
            self.analytics.preview([model["id"]])

    def test_cross_volume_copy_preserves_contents_before_removing_original(self):
        plan = self.analytics.preview([self.models()[0]["id"]])
        with patch("abg_analytics.moving.os.link", side_effect=OSError(errno.EXDEV, "Different volume")):
            result = self.analytics.trash(plan["plan_id"])["results"][0]
        self.assertTrue(result["success"])
        self.assertEqual(Path(result["destination"]).stat().st_size, plan["files"][0]["size_bytes"])
        self.assertFalse(Path(result["source"]).exists())

    def test_copy_failure_keeps_source_and_cleans_partial_destination(self):
        plan = self.analytics.preview([self.models()[0]["id"]])
        with (
            patch("abg_analytics.moving.os.link", side_effect=OSError(errno.EXDEV, "Different volume")),
            patch("abg_analytics.moving.shutil.copyfileobj", side_effect=OSError(errno.ENOSPC, "Full disk")),
        ):
            result = self.analytics.trash(plan["plan_id"])["results"][0]
        self.assertFalse(result["success"])
        self.assertTrue(Path(plan["files"][0]["source"]).exists())
        self.assertFalse(Path(plan["files"][0]["destination"]).exists())

    def test_failed_load_and_failed_execution_are_not_recorded_and_cache_reuse_is(self):
        analytics = self.analytics

        class Executor:
            success = False

            async def execute_async(self, prompt, prompt_id, extra_data=None, execute_outputs=None):
                self.success = prompt_id == "success"
                return "output"

        utils = types.ModuleType("comfy.utils")
        utils.load_torch_file = lambda ckpt: (
            (_ for _ in ()).throw(ValueError("bad weights")) if ckpt == "broken" else {"weights": 1}
        )
        comfy = types.ModuleType("comfy")
        comfy.utils = utils
        execution = types.ModuleType("execution")
        execution.PromptExecutor = Executor
        with patch.dict(sys.modules, {"comfy": comfy, "comfy.utils": utils, "execution": execution}):
            install_hooks(analytics)
            install_hooks(analytics)  # Idempotent; no double wrapping.
            with self.assertRaises(ValueError):
                utils.load_torch_file("broken")
            prompt = {"1": {"inputs": {"lora_name": "flux2/same.safetensors"}}, "2": {"inputs": {"model": ["1", 0]}}}
            asyncio.run(Executor().execute_async(prompt, "failure", execute_outputs=["2"]))
            self.assertFalse(analytics.store.history())
            asyncio.run(Executor().execute_async(prompt, "success", execute_outputs=["2"]))
            self.assertIn(path_key(self.local), analytics.store.history())
            self.assertNotIn(path_key(self.external), analytics.store.history())
            utils.load_torch_file(self.external)
            self.assertIsNotNone(analytics.store.history()[path_key(self.external)]["last_loaded"])
            self.assertEqual(analytics.active_executions, 0)

    def test_unconnected_and_ambiguous_workflow_references_are_not_recorded(self):
        checkpoint = self.make_file(Path(self.folders.models_dir) / "checkpoints" / "flux2" / self.local.name)
        prompt = {
            "1": {"inputs": {"model_name": "flux2/same.safetensors"}},
            "2": {"inputs": {"model": ["1", 0]}},
            "3": {"inputs": {"ckpt_name": "flux2/same.safetensors"}},
        }
        self.assertFalse(referenced_paths(self.folders, prompt, ["2"]))
        self.assertEqual(referenced_paths(self.folders, prompt, ["3"]), {path_key(checkpoint)})

    def test_missing_directory_is_reported_without_losing_other_models(self):
        self.folders.folder_names_and_paths["missing"] = ([str(self.base / "missing")], {".gguf"})
        data = self.analytics.inventory()
        self.assertEqual(len(data["models"]), 2)
        self.assertTrue(any("unavailable" in warning for warning in data["warnings"]))


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        self.temporary = tempfile.TemporaryDirectory()
        self.folders = Folders(self.temporary.name)
        self.analytics = Analytics(self.folders)
        app = web.Application()
        routes = web.RouteTableDef()
        register_routes(routes, self.analytics)
        app.add_routes(routes)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temporary.cleanup()

    async def test_settings_inventory_and_invalid_mutation(self):
        response = await self.client.post(
            PREFIX + "/settings", json={"trash_directory": str(Path(self.temporary.name) / "cleanup"), "stale_days": 14}
        )
        self.assertEqual(response.status, 200)
        response = await self.client.get(PREFIX + "/inventory")
        self.assertEqual((await response.json())["settings"]["stale_days"], 14)
        response = await self.client.post(PREFIX + "/trash", data="plan_id=anything")
        self.assertEqual(response.status, 400)
        response = await self.client.post(PREFIX + "/settings", json={"stale_days": None})
        self.assertEqual(response.status, 400)
        response = await self.client.post(PREFIX + "/trash/preview", json=[])
        self.assertEqual(response.status, 400)

    async def test_api_preview_trash_and_restore(self):
        source = Path(self.folders.models_dir) / "loras" / "test.safetensors"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"test weights")
        self.analytics.configure({"trash_directory": str(Path(self.temporary.name) / "cleanup")})
        response = await self.client.get(PREFIX + "/inventory")
        model = (await response.json())["models"][0]
        response = await self.client.post(PREFIX + "/trash/preview", json={"model_ids": [model["id"]]})
        preview = await response.json()
        self.assertTrue(source.exists())
        response = await self.client.post(PREFIX + "/trash", json={"plan_id": preview["plan_id"]})
        result = (await response.json())["results"][0]
        self.assertTrue(result["success"])
        self.assertFalse(source.exists())
        response = await self.client.post(PREFIX + "/restore", json={"move_id": result["id"]})
        self.assertEqual(response.status, 200)
        self.assertEqual(source.read_bytes(), b"test weights")

    async def test_api_protection_duplicate_scan_and_trash_summary(self):
        source = Path(self.folders.models_dir) / "loras" / "one.safetensors"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"same weights")
        source.with_name("two.safetensors").write_bytes(source.read_bytes())
        response = await self.client.get(PREFIX + "/inventory")
        model = (await response.json())["models"][0]
        response = await self.client.post(PREFIX + "/protection", json={"model_id": model["id"], "protected": True})
        self.assertEqual(response.status, 200)
        response = await self.client.get(PREFIX + "/inventory")
        self.assertTrue(any(model["protected"] for model in (await response.json())["models"]))
        response = await self.client.post(PREFIX + "/duplicates/scan", json={})
        self.assertEqual((await response.json())["verified_groups"], 1)
        response = await self.client.get(PREFIX + "/trash/summary")
        self.assertEqual((await response.json())["size_bytes"], 0)
        response = await self.client.post(PREFIX + "/protection", json={"model_id": model["id"], "protected": "yes"})
        self.assertEqual(response.status, 400)


if __name__ == "__main__":
    unittest.main()
