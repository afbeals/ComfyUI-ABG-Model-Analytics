"""Generate a standalone interactive preview using the actual extension UI.

All API behavior is simulated in memory. No server or ComfyUI is required.
"""

import json
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]


def build(output=None):
    specs = [
        ("diffusion_models", "flux/flux2-dev-fp8.safetensors", 22.4, 78, r"D:\AI\ComfyUI\models"),
        ("checkpoints", "sdxl/juggernautXL_v9.safetensors", 6.46, 63, r"D:\AI\ComfyUI\models"),
        ("loras", "flux2/cinematic-light-v2.safetensors", 0.78, 44, r"D:\AI\ComfyUI\models"),
        ("controlnet", "sdxl/depth-controlnet.safetensors", 2.49, 91, r"E:\SharedModels"),
        ("upscale_models", "4x_RealESRGAN.pth", 0.064, 112, r"D:\AI\ComfyUI\models"),
        ("diffusion_models", "flux/flux2-dev-Q5_K_M.gguf", 15.9, None, r"E:\SharedModels"),
        ("loras", "flux2/portrait-studio.safetensors", 0.41, None, r"D:\AI\ComfyUI\models"),
        ("vae", "sdxl/sdxl_vae.safetensors", 0.32, None, r"D:\AI\ComfyUI\models"),
        ("diffusion_models", "flux/flux1-schnell.safetensors", 11.9, 0, r"D:\AI\ComfyUI\models"),
        ("loras", "flux2/cinematic-light-v2.safetensors", 0.78, 2, r"E:\SharedModels"),
        ("text_encoders", "t5xxl_fp16.safetensors", 9.1, 1, r"D:\AI\ComfyUI\models"),
        ("vae", "flux/ae.safetensors", 0.31, 0, r"D:\AI\ComfyUI\models"),
    ]
    models = []
    for index, (kind, name, size, days, base) in enumerate(specs):
        root = PureWindowsPath(base) / kind
        models.append(
            {
                "id": f"sample-model-{index}",
                "kind": kind,
                "kinds": [kind],
                "path": str(root / name),
                "root": str(root),
                "relative_path": name,
                "logical_path": f"models/{kind}/{name}",
                "size_bytes": int(size * 1024**3),
                "disabled": False,
                "can_move": True,
                "move_reason": "",
                "days": days,
            }
        )
    frontend = (ROOT / "web/js/main.js").read_text()
    frontend = "\n".join(line for line in frontend.splitlines() if not line.startswith("import "))
    adapter = (ROOT / "scripts/preview_api.js").read_text().replace("__MODELS__", json.dumps(models))
    page = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ABG Model Analytics — Interactive Preview</title>
<link data-abg-analytics rel="stylesheet" href="data:text/css,">
<style>__CSS__
body{margin:0;background:#11151c;color:#dce7f5;font:14px system-ui;min-height:100vh}
.preview-shell{padding:36px;max-width:1000px;margin:auto}.preview-shell h1{font-size:28px}
.preview-shell p{color:#b6c5d9;line-height:1.7}.preview-shell button{border:1px solid #698577;background:#a2e5cd;border-radius:8px;padding:12px 20px;cursor:pointer;color:#172d25;font-weight:600}
</style></head><body><main class="preview-shell"><h1>ABG Model Analytics</h1><p>Interactive preview · sample Windows model library</p><p>Try filters, cleanup settings, selection, move previews, simulated trash moves, and restores. Everything stays in memory. This page never accesses your model files. Reload to reset the sample library.</p><div id="preview-launcher"></div></main>
<script type="module">__ADAPTER__
__FRONTEND__
dashboard.open();
const previewBadge = document.createElement('span'); previewBadge.className = 'abg-preview-badge'; previewBadge.textContent = 'PREVIEW · SAMPLE DATA';
dashboard.dialog.querySelector('h2').append(previewBadge);
</script></body></html>"""
    page = (
        page.replace("__CSS__", (ROOT / "web/css/style.css").read_text())
        .replace("__ADAPTER__", adapter)
        .replace("__FRONTEND__", frontend)
    )
    output = Path(output or ROOT / "preview/index.html")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page)
    print(f"Built standalone interactive preview: {output}")


if __name__ == "__main__":
    build()
