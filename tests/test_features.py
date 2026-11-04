import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from abg_analytics.inventory import path_key
from abg_analytics.service import Analytics
from abg_analytics.trash import summarize_trash
from tests.test_analytics import Folders


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.folders = Folders(self.base)
        self.analytics = Analytics(self.folders)
        self.local = self.write(Path(self.folders.models_dir) / "loras" / "flux2" / "model.safetensors", b"weights-one")
        self.extra = self.write(
            self.base / "external" / "models" / "loras" / "flux2" / "model.safetensors", b"weights-two"
        )
        self.analytics.configure({"trash_directory": str(self.base / "cleanup")})

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, path, contents):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return path

    def workflow(self, name, data):
        return self.write(self.base / "user" / name, json.dumps(data).encode())

    def model(self, path=None):
        return next(model for model in self.analytics.inventory()["models"] if model["path"] == str(path or self.local))

    def test_protection_survives_restart_and_blocks_cleanup(self):
        model = self.model()
        self.analytics.protect(model["id"], True)
        self.analytics = Analytics(self.folders)
        protected = self.model()
        self.assertTrue(protected["protected"])
        self.assertFalse(protected["can_move"])
        with self.assertRaisesRegex(ValueError, "Protected"):
            self.analytics.preview([model["id"]])
        self.analytics.protect(model["id"], False)
        self.assertTrue(self.model()["can_move"])

    def test_protection_invalidates_existing_preview(self):
        model = self.model()
        plan = self.analytics.preview([model["id"]])
        self.analytics.protect(model["id"], True)
        with self.assertRaises(ValueError):
            self.analytics.trash(plan["plan_id"])
        self.assertTrue(self.local.exists())

    def test_replacement_at_a_protected_path_stays_protected(self):
        self.analytics.protect(self.model()["id"], True)
        self.local.write_bytes(b"updated model weights")
        self.assertTrue(self.model()["protected"])
        self.assertFalse(self.model()["can_move"])

    def test_nested_roots_keep_both_valid_relative_names_for_saved_workflows(self):
        self.folders.folder_names_and_paths["loras"][0].append(str(self.local.parent))
        self.workflow(
            "default/workflows/nested.json",
            {"nodes": [{"type": "LoraLoader", "widgets_values": ["model.safetensors"]}]},
        )
        self.assertEqual(self.model()["workflows"][0]["match"], "resolved")

    def test_protection_is_rechecked_even_if_changed_outside_service(self):
        plan = self.analytics.preview([self.model()["id"]])
        self.analytics.store.protect(path_key(self.local), True)
        with self.assertRaises(ValueError):
            self.analytics.trash(plan["plan_id"])
        self.assertTrue(self.local.exists())

    def test_protection_rejects_invalid_or_arbitrary_paths(self):
        for model_id, protected in [(self.model()["id"], "true"), (str(self.local), True), (None, False)]:
            with self.subTest(model_id=model_id), self.assertRaises(ValueError):
                self.analytics.protect(model_id, protected)

    def test_ui_and_api_workflows_across_profiles_and_nested_folders_are_discovered(self):
        self.workflow(
            "default/workflows/portraits/Flux.json",
            {"nodes": [{"type": "LoraLoader", "widgets_values": ["flux2\\model.safetensors", 0.8]}]},
        )
        self.workflow(
            "other-user/workflows/API.json",
            {"1": {"class_type": "LoraLoader", "inputs": {"lora_name": "flux2/model.safetensors"}}},
        )
        self.workflow(
            "workflows/Legacy.json",
            {"prompt": {"1": {"class_type": "LoraLoader", "inputs": {"lora_name": "flux2/model.safetensors"}}}},
        )
        data = self.analytics.inventory()
        self.assertEqual(len(data["workflow_scan"]["workflows"]), 3)
        local = next(model for model in data["models"] if model["path"] == str(self.local))
        extra = next(model for model in data["models"] if model["path"] == str(self.extra))
        self.assertTrue(all(item["match"] == "resolved" for item in local["workflows"]))
        self.assertTrue(all(item["match"] == "fallback" for item in extra["workflows"]))
        self.assertEqual(local["status"], "unknown")  # A saved file alone is not activity.

    def test_unknown_loader_reference_is_flagged_possible_and_basename_is_not_guessed(self):
        self.workflow(
            "default/workflows/custom.json",
            {
                "nodes": [
                    {"type": "CustomLoader", "widgets_values": ["flux2/model.safetensors"]},
                    {"type": "CustomLoader", "widgets_values": ["model.safetensors"]},
                ]
            },
        )
        model = self.model()
        self.assertEqual(len(model["workflows"]), 1)
        self.assertEqual(model["workflows"][0]["match"], "possible")

    def test_corrupt_workflow_does_not_hide_valid_workflow_or_models(self):
        self.write(self.base / "user" / "default" / "workflows" / "broken.json", b"{broken")
        self.workflow(
            "default/workflows/valid.json",
            {"nodes": [{"type": "LoraLoader", "widgets_values": ["flux2/model.safetensors"]}]},
        )
        data = self.analytics.inventory()
        self.assertEqual(len(data["models"]), 2)
        self.assertEqual(len(data["workflow_scan"]["workflows"]), 1)
        self.assertTrue(any("broken.json" in warning for warning in data["warnings"]))

    def test_malformed_node_type_does_not_crash_inventory(self):
        self.workflow(
            "default/workflows/malformed.json", {"nodes": [{"type": [], "widgets_values": ["flux2/model.safetensors"]}]}
        )
        self.assertEqual(len(self.analytics.inventory()["models"]), 2)

    def test_preview_contains_workflow_impact_and_new_reference_invalidates_it(self):
        self.workflow(
            "default/workflows/one.json",
            {"nodes": [{"type": "LoraLoader", "widgets_values": ["flux2/model.safetensors"]}]},
        )
        plan = self.analytics.preview([self.model()["id"]])
        self.assertEqual(plan["files"][0]["workflows"][0]["match"], "resolved")
        self.workflow(
            "default/workflows/two.json",
            {"nodes": [{"type": "LoraLoader", "widgets_values": ["flux2/model.safetensors"]}]},
        )
        with self.assertRaisesRegex(ValueError, "workflow references changed"):
            self.analytics.trash(plan["plan_id"])

    def test_same_name_is_separate_from_verified_identical_content(self):
        model = self.model()
        self.assertEqual(model["copies"][0]["role"], "active")
        self.assertFalse(model["duplicate_paths"])
        self.assertTrue(self.model(self.extra)["shadowed"])
        result = self.analytics.find_duplicates()  # Same length; different bytes.
        self.assertEqual(result["hashed_files"], 2)
        self.assertEqual(result["verified_groups"], 0)

    def test_shadowed_file_becomes_active_when_higher_priority_copy_is_moved(self):
        self.assertTrue(self.model(self.extra)["shadowed"])
        plan = self.analytics.preview([self.model()["id"]])
        self.analytics.trash(plan["plan_id"])
        self.assertFalse(self.model(self.extra)["shadowed"])
        self.assertFalse(self.model(self.extra)["copies"])

    def test_hash_check_finds_differently_named_identical_files_and_caches_results(self):
        renamed = self.write(Path(self.folders.models_dir) / "loras" / "renamed.safetensors", self.local.read_bytes())
        result = self.analytics.find_duplicates()
        self.assertEqual(result["verified_groups"], 1)
        self.assertEqual(self.model()["duplicate_paths"], [str(renamed)])
        result = self.analytics.find_duplicates()
        self.assertEqual(result["hashed_files"], 0)
        self.assertEqual(result["cached_files"], 3)
        self.local.write_bytes(b"replacement-weights")
        self.assertFalse(self.model()["duplicate_paths"])
        self.assertFalse(self.model(renamed)["duplicate_paths"])

    def test_file_changed_while_hashing_is_not_reported_verified(self):
        from abg_analytics.inventory import fingerprint

        original = fingerprint
        calls = {}

        def changed(path):
            key = str(path)
            calls[key] = calls.get(key, 0) + 1
            value = original(path)
            if calls[key] > 1:
                value[-1] += 1
            return value

        with patch("abg_analytics.copies.fingerprint", side_effect=changed):
            result = self.analytics.find_duplicates()
        self.assertEqual(result["hashed_files"], 0)
        self.assertEqual(len(result["warnings"]), 2)

    def test_trash_size_counts_actual_files_and_updates_after_restore_or_manual_removal(self):
        plan = self.analytics.preview([self.model()["id"]])
        move = self.analytics.trash(plan["plan_id"])["results"][0]
        unrelated = self.write(self.base / "cleanup" / "trash" / "notes.txt", b"notes")
        summary = self.analytics.trash_summary()
        self.assertEqual(summary["file_count"], 2)
        self.assertEqual(summary["size_bytes"], len(b"weights-one") + len(b"notes"))
        self.analytics.restore(move["id"])
        self.assertEqual(self.analytics.trash_summary()["size_bytes"], len(b"notes"))
        unrelated.unlink()
        self.assertEqual(self.analytics.trash_summary()["file_count"], 0)

    def test_trash_size_preserves_previous_locations_after_setting_changes_and_restart(self):
        self.write(self.base / "cleanup" / "trash" / "old.bin", b"old")
        self.analytics.configure({"trash_directory": str(self.base / "new-cleanup")})
        self.write(self.base / "new-cleanup" / "trash" / "new.bin", b"new-file")
        self.analytics = Analytics(self.folders)
        summary = self.analytics.trash_summary()
        self.assertEqual(summary["file_count"], 2)
        self.assertEqual(summary["size_bytes"], 11)
        self.assertEqual(len(summary["locations"]), 2)
        self.assertEqual(sum(row["current"] for row in summary["locations"]), 1)

    def test_overlapping_trash_locations_are_not_double_counted(self):
        root = self.base / "trash"
        self.write(root / "nested" / "trash" / "model.bin", b"weights")
        summary = summarize_trash([str(root), str(root / "nested" / "trash")])
        self.assertEqual(summary["file_count"], 1)
        self.assertEqual(summary["size_bytes"], 7)

    @unittest.skipIf(os.name == "nt", "Windows symlink creation needs elevation or developer mode")
    def test_trash_does_not_follow_links_outside_it(self):
        outside = self.write(self.base / "outside" / "keep.bin", b"large-external-file")
        root = self.base / "cleanup" / "trash"
        root.mkdir(parents=True)
        (root / "linked.bin").symlink_to(outside)
        (root / "linked-folder").symlink_to(outside.parent, target_is_directory=True)
        summary = self.analytics.trash_summary()
        self.assertEqual(summary["file_count"], 0)
        self.assertFalse(summary["complete"])
        self.assertEqual(len(summary["warnings"]), 2)

    def test_old_trash_is_excluded_even_if_a_new_model_root_contains_it(self):
        old = self.write(self.base / "cleanup" / "trash" / "old.safetensors", b"old")
        self.analytics.configure({"trash_directory": str(self.base / "new-cleanup")})
        self.folders.folder_names_and_paths["misc"] = ([str(self.base / "cleanup")], {".safetensors"})
        self.assertNotIn(str(old), {model["path"] for model in self.analytics.inventory()["models"]})
