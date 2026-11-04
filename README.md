# ComfyUI ABG Model Analytics

Personal model usage analytics and reversible cleanup for ComfyUI, including **ComfyUI Desktop on Windows** and custom model directories.

Inspired by [Bennowan’s ComfyUI-Model-Analytics](https://github.com/Bennowan/ComfyUI-Model-Analytics). Credit to Bennowan for the original extension; its non-commercial license conditions are retained. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Try the UI before installing

Open [the standalone interactive preview](preview/index.html) in a browser. It uses the same dashboard code with a sample Windows model library. Search/filter, change settings, select models, review move destinations, simulate trash moves, and restore them. Everything stays in memory; it never accesses real model files. Reload to reset the sample data.

After frontend changes, regenerate it with `python scripts/build_preview.py`.

## What it does

- Discovers model paths from ComfyUI’s live `folder_paths` registry, including extra model paths, Desktop’s custom base directory, and paths added by other nodes. Also scans unregistered subdirectories within the default models tree.
- Tracks each file by its full path, keeping same-named models in different directories separate.
- Shows **Not recently used**, **Unknown history**, **Recently used**, and **All models**, with search, source/category filters, and sorting. The recency window defaults to 30 days and is configurable.
- Moves only selected files into a configurable trash directory, with an explicit preview of each source and destination. Nothing is moved automatically.
- Preserves `models/<category>/<subfolders>/<filename>` and provides **Trash & history → Restore**.
- Requires ComfyUI’s generation queue to be idle before trashing or restoring.
- Protects/pins models persistently, blocking cleanup in both the UI and backend.
- Lists saved workflow references and warns about affected workflows before moving files.
- Identifies same-name and shadowed copies, with an optional SHA-256 check for identical contents.
- Measures the files currently in trash, including previous trash locations after a settings change.

## Install on Windows ComfyUI Desktop

Use the **data/base directory you selected in Desktop**, which contains your `custom_nodes` directory. Do not install into Desktop’s bundled application resources.

To find the base directory in PowerShell:

```powershell
$desktopConfig = Get-Content (Join-Path $env:APPDATA 'ComfyUI\config.json') -Raw | ConvertFrom-Json
$comfyBase = $desktopConfig.basePath
$comfyBase
```

If your setup has custom node paths beyond that base directory, use the actual `custom_nodes` location shown in ComfyUI’s startup log.

### Install from the supplied ZIP

1. Fully quit ComfyUI Desktop.
2. Extract the ZIP and place its `ComfyUI-ABG-Model-Analytics` folder inside `<your-base-directory>\custom_nodes\`.
3. Verify the layout is `custom_nodes\ComfyUI-ABG-Model-Analytics\__init__.py`, with no extra nested folder.
4. Restart Desktop. Open the **ABG Model Analytics** chart icon in the sidebar, then click **Open ABG Model Analytics**. It is also registered in the Extensions menu on modern frontends. Older frontends get a menu or floating button.

No additional Python dependencies should be necessary; ComfyUI already includes `aiohttp`. No GPU libraries are added.

### Install from GitHub after the changes are published

With Git installed, run in PowerShell:

```powershell
$customNodes = Join-Path $comfyBase 'custom_nodes'
if (!(Test-Path $customNodes -PathType Container)) { throw 'Choose your actual custom_nodes directory first.' }
Set-Location $customNodes
git clone https://github.com/afbeals/ComfyUI-ABG-Model-Analytics.git
```

If the repository is private, authenticate Git using your own GitHub account. No GitHub token is needed by the extension itself. Restart Desktop after installation or updates.

## Configure and clean up

1. Open the dashboard and set **Trash parent directory**, for example `D:\ComfyCleanup`. This is a path on the **Windows computer running ComfyUI**.
2. Set **Recent within (days)** and click **Save settings**. Settings persist across restarts.
3. Check **Directories** to verify every model location you expect is listed. Missing/inaccessible paths and skipped links appear as scan notes.
4. Use **Protect** to pin models you want to keep. Protected models cannot be selected or moved; use the **Protected** tab to review them.
5. Choose a model view, select files, and click **Preview move**. Review the source/destination paths and saved-workflow warnings, then confirm **Move to trash**.
6. Use **Trash & history → Restore** to undo a completed move. Stop running/queued generations before moving or restoring.

For example:

```text
Source:  E:\ComfyUI\models\loras\flux2\example.safetensors
Setting: D:\ComfyCleanup
Target:  D:\ComfyCleanup\trash\models\loras\flux2\example.safetensors
```

Extra model directories follow the same rule when they have a `models` ancestor. If a registered model directory has no `models` ancestor, the extension uses `models/<registered-category>/<path-relative-to-that-directory>`.

When two sources would have the same destination, or a destination already exists, the preview puts the additional file under `trash/collisions/<source-id>-<number>/models/…`. Existing files are never overwritten. Restoring uses the original absolute path recorded in the move journal, even if you later change the trash setting.

Trash must be outside configured model directories. For example, `D:\ComfyCleanup` is suitable; `E:\ComfyUI\models\cleanup` is not. Choose an existing drive with room for cross-drive copies. The extension creates missing subfolders when a move is confirmed.

To add **model source locations**, configure them in ComfyUI/Desktop and restart; the extension reads those settings automatically. Desktop stores its extra model configuration in `%APPDATA%\ComfyUI\extra_models_config.yaml`; standalone installations commonly use `extra_model_paths.yaml`. There is no separate list of model directories to maintain in this extension.

## Protection, saved workflows, and copies

**Protection/pinning:** click **Protect** beside a model to keep it out of cleanup selections. Protection is saved in the database by the file’s full path, survives restarts and replacements at that path, and is rechecked before moving. Changing protection invalidates existing move previews. Manually moving or renaming a file gives it a different path and requires protecting it there.

**Saved workflow references:** expand the details under a model or open **Saved workflows**. The scanner reads JSON workflows under the ComfyUI user directory’s `workflows`, `default/workflows`, and other local user-profile `workflows` directories, including subfolders. Both UI-format workflows and API-format prompts are supported. It does not search arbitrary export folders elsewhere on disk, and it skips linked workflow folders/files and workflows larger than 32 MiB.

- **Resolves to this file:** a known loader/input identifies the model category, and ComfyUI resolves its relative filename to this file.
- **Fallback copy:** another file currently has priority for that filename. This copy could be used if the higher-priority file is removed.
- **Possible reference:** a custom/unknown loader or absolute path cannot be attributed with the same confidence.

The saved-workflow dropdown filters the combined inventory. Moving a referenced file shows the workflow names in the confirmation. References are rechecked when the move starts; changed references require a fresh preview. Saved references alone do **not** update last-used timestamps. Unsupported custom loaders, dynamically generated names, unsaved workflows, and missing references may not be identified, so the reference list is not a complete dependency guarantee.

**Same-name and shadowed copies:** the **Copies** view shows models with the same category-relative filename in multiple roots. ComfyUI’s own resolver determines the active location. Expand a row to see the active path and other copies. Different names are kept separate, and matching names do not imply matching contents.

**Exact duplicates:** click **Check exact duplicates** in the Copies view to compare files with equal sizes using SHA-256. This can find identical weights with different filenames. The check reads candidates’ contents, can take time for large models, and caches results until their file identity, size, or modification time changes. It runs only when requested and never deletes or moves files. Hard links can have identical contents without consuming separate physical storage, so duplicate file sizes are not presented as guaranteed reclaimable space.

## How usage is measured

The extension records two kinds of evidence, displayed separately:

- **Loaded:** a successful call to ComfyUI’s `comfy.utils.load_torch_file`. This reports a successful weight-file read, not necessarily a VRAM transfer or a completed generation. Failed loads are not recorded.
- **Workflow reference:** an exact file reference in the output dependency graph of a successful workflow. This refreshes recency even when the model loader is cached. Failed or merely queued workflows do not generate this evidence. Lazy branches can be referenced without being evaluated; this deliberately favors keeping a potentially used file.

The latest of these determines recency. Replacing or modifying a file at the same path resets its observed usage until it is used again, based on its size and modification time. ComfyUI’s own path resolution order identifies the active file when multiple roots contain the same relative name. Ambiguous references across model categories are skipped rather than assigned by basename. A few common loader input names provide category hints.

**Unknown history does not mean never used.** Tracking starts when this extension is first installed. Filesystem access times are not used, and the original extension’s basename-based history is not imported. Third-party loaders that bypass `load_torch_file`, dynamically generated filenames, embeddings named only in prompt text, or custom cache behavior may not be observed. Treat usage as evidence to review, not a guarantee that a file can be removed.

Supported model files include `.safetensors`, `.gguf`, `.ckpt`, `.pt`, `.pt2`, `.pth`, `.bin`, `.sft`, `.pkl`, `.onnx`, `.engine`, `.tflite`, and additional extensions registered by model categories. Disabled files with a `.disabled` suffix are included. Configuration files, previews, and other sidecars are left in place. Sharded models appear as individual weight files; select the complete set when moving them.

## File moves and recovery

- On the same volume, moves use a hard link followed by unlinking the original filename, without reading/copying the model’s contents. When hard links are unavailable or the destination is on another drive, a complete copy is flushed to disk and checked for size/source changes before the source is removed.
- A copy failure keeps the source and attempts to clean up its partial destination. Cross-drive operations may take a while; let the confirmation finish.
- Linked files cannot be moved. Nested symlink/junction directories are skipped with a scan note. Explicitly registered roots are resolved to their actual targets, so registering the target directly is supported.
- Restore refuses to replace an existing original file.
- The extension never permanently deletes trash. To reclaim the trash drive’s space, review and empty it yourself. Moving to another folder on the same drive does not free that drive’s space.
- The **Trash** overview card and **Trash & history** tab sum the sizes of regular files actually present in current and previous trash locations, including files placed there manually. Totals update on rescans, moves, and restores. These are logical file sizes, not a measurement of unique allocated disk blocks. Linked contents are skipped, overlapping locations are counted once, and unavailable/partially scanned locations are identified. Changing the trash setting does not move existing trash.
- Each operation is journaled before it starts. After a crash/power loss, a `pending` or `restoring` record may require manual recovery using the displayed original/trash paths. The batch is not a single filesystem transaction: completed files stay moved if a later file fails.

Settings, usage, and the move journal are stored in:

```text
<ComfyUI user directory>/abg-model-analytics/analytics.sqlite3
```

This is separate from the extension’s code and survives code updates. Back it up along with your ComfyUI user directory. Moving the user directory itself requires preserving this database. Model path changes create new identities; old entries are retained for history.

## Update or uninstall

For a Git installation, quit Desktop, run `git pull` inside this extension’s directory, and restart. For a ZIP installation, replace the extension directory with the new version. Restore desired files before uninstalling; removing the extension does not remove the trash or the database.

To uninstall, quit Desktop, move this extension out of `custom_nodes`, and restart. The original Bennowan extension can coexist because API, UI, and storage names are separate; disable it if you no longer want two dashboards.

## Development and verification

Tests use temporary dummy models and do not require a GPU or a ComfyUI installation:

```bash
python -m venv .venv
# Activate .venv using your shell's usual command.
python -m pip install -r requirements.txt ruff playwright
python -m unittest discover -s tests -v
ruff check .
ruff format --check .
node --check web/js/main.js
playwright install chromium
python -m tests.browser_smoke
python scripts/build_preview.py
python -m tests.preview_smoke
```

The browser smoke test runs the actual dashboard and API against a small ComfyUI host adapter, covering protection, saved workflows, shadowing, exact duplicates, workflow-impact warnings, trash totals, settings, root discovery, preview/cancel, trash/restore, filtering, narrow screens, and keyboard dismissal. A separate browser test exercises the standalone simulated preview. These tests do not establish compatibility with a particular installed Desktop build. The first Windows installation still needs a startup/UI check and a test with one disposable model file.
