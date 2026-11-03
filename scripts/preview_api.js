// Standalone preview adapter. All operations below use sample data in memory.
const samples = __MODELS__;
const initialTime = Date.now();
for (const [index, model] of samples.entries()) {
  model.last_used =
    model.days === null ? null : new Date(initialTime - model.days * 86400000).toISOString();
  model.last_loaded = model.last_used;
  model.last_referenced = model.last_used;
  model.use_count = model.days === null ? 0 : 5;
  model.protected = [8, 10, 11].includes(index);
  model.contentSignature = [2, 9].includes(index) ? 'cinematic-identical-weights' : model.id;
}
const savedSamples = [
  {
    name: 'default/workflows/portraits/Flux portrait.json',
    references: [
      ['diffusion_models', 'flux/flux2-dev-fp8.safetensors'],
      ['loras', 'flux2/cinematic-light-v2.safetensors'],
      ['text_encoders', 't5xxl_fp16.safetensors'],
      ['vae', 'flux/ae.safetensors'],
    ],
  },
  {
    name: 'default/workflows/SDXL product photos.json',
    references: [
      ['checkpoints', 'sdxl/juggernautXL_v9.safetensors'],
      ['controlnet', 'sdxl/depth-controlnet.safetensors'],
      ['vae', 'sdxl/sdxl_vae.safetensors'],
    ],
  },
  {
    name: 'default/workflows/Quick drafts.json',
    references: [
      ['diffusion_models', 'flux/flux1-schnell.safetensors'],
      ['text_encoders', 't5xxl_fp16.safetensors'],
      ['vae', 'flux/ae.safetensors'],
    ],
  },
  {
    name: 'default/workflows/experiments/Custom GGUF.json',
    references: [[null, 'flux/flux2-dev-Q5_K_M.gguf']],
  },
].map((workflow) => ({
  ...workflow,
  path: `D:\\AI\\ComfyUI\\user\\${workflow.name.replaceAll('/', '\\')}`,
}));
const previewState = {
  models: structuredClone(samples),
  moves: [],
  plans: new Map(),
  checked: new Set(),
  trashLocations: new Set(['F:\\ModelArchive\\trash', 'G:\\PreviousArchive\\trash']),
  settings: {
    trash_directory: 'F:\\ModelArchive',
    stale_days: 30,
    tracking_since: new Date(initialTime - 120 * 86400000).toISOString(),
  },
};
for (const [index, location, size] of [
  [0, 'F:\\ModelArchive', 3.4],
  [1, 'G:\\PreviousArchive', 1.2],
]) {
  const model = {
    ...structuredClone(samples[2]),
    id: `archived-sample-${index}`,
    path: `D:\\AI\\ComfyUI\\models\\loras\\archive\\retired-style-${index}.safetensors`,
    relative_path: `archive/retired-style-${index}.safetensors`,
    logical_path: `models/loras/archive/retired-style-${index}.safetensors`,
    size_bytes: Math.round(size * 1024 ** 3),
    protected: false,
  };
  previewState.moves.push({
    id: `old-preview-move-${index}`,
    source: model.path,
    destination: `${location}\\trash\\${model.logical_path.replaceAll('/', '\\')}`,
    size_bytes: model.size_bytes,
    created_at: new Date(initialTime - (index + 2) * 86400000).toISOString(),
    status: 'trashed',
    model,
  });
}

function rebuildPreview() {
  const groups = new Map();
  for (const model of previewState.models) {
    model.status = !model.last_used
      ? 'unknown'
      : (Date.now() - new Date(model.last_used).getTime()) / 86400000 >
          previewState.settings.stale_days
        ? 'stale'
        : 'recent';
    model.can_move = !model.protected;
    model.move_reason = model.protected
      ? 'Protected model. Remove protection before moving it.'
      : '';
    model.workflows = [];
    model.copies = [];
    model.duplicate_paths = [];
    model.shadowed = false;
    model.hash_checked = previewState.checked.has(model.id);
    const key = `${model.kind}:${model.relative_path.toLowerCase()}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(model);
  }
  for (const group of groups.values()) {
    group.sort((a, b) => a.path.localeCompare(b.path));
    if (group.length < 2) continue;
    for (const model of group) {
      model.shadowed = model.id !== group[0].id;
      model.copies.push({
        kind: model.kind,
        name: model.relative_path,
        role: model.shadowed ? 'shadowed' : 'active',
        active_path: group[0].path,
        other_paths: group.filter((other) => other.id !== model.id).map((other) => other.path),
      });
    }
  }
  const hashes = new Map();
  for (const model of previewState.models.filter((model) => model.hash_checked)) {
    if (!hashes.has(model.contentSignature)) hashes.set(model.contentSignature, []);
    hashes.get(model.contentSignature).push(model);
  }
  for (const group of hashes.values()) {
    if (group.length > 1)
      for (const model of group)
        model.duplicate_paths = group
          .filter((other) => other.id !== model.id)
          .map((other) => other.path);
  }
  const workflows = savedSamples.map((workflow) => {
    const references = new Map();
    for (const [kind, name] of workflow.references) {
      const candidates = kind
        ? groups.get(`${kind}:${name.toLowerCase()}`) || []
        : previewState.models.filter(
            (model) => model.relative_path.toLowerCase() === name.toLowerCase(),
          );
      for (const [index, model] of candidates.entries())
        references.set(model.id, kind ? (index === 0 ? 'resolved' : 'fallback') : 'possible');
    }
    const models = [...references].map(([id, match]) => ({ id, match }));
    for (const reference of models)
      previewState.models
        .find((model) => model.id === reference.id)
        .workflows.push({ name: workflow.name, path: workflow.path, match: reference.match });
    return { name: workflow.name, path: workflow.path, models };
  });
  return {
    workflows,
    copies: {
      same_name_groups: [...groups.values()].filter((group) => group.length > 1).length,
      verified_groups: [...hashes.values()].filter((group) => group.length > 1).length,
      checked_files: previewState.models.filter((model) => model.hash_checked).length,
    },
  };
}

function previewTrash() {
  const current = `${previewState.settings.trash_directory.replace(/[\\/]+$/, '')}\\trash`;
  const locations = [...previewState.trashLocations].map((path) => {
    const files = previewState.moves.filter(
      (move) => move.status === 'trashed' && move.destination.startsWith(`${path}\\`),
    );
    return {
      path,
      current: path === current,
      exists: true,
      size_bytes: files.reduce((sum, file) => sum + file.size_bytes, 0),
      file_count: files.length,
    };
  });
  return {
    size_bytes: locations.reduce((sum, location) => sum + location.size_bytes, 0),
    file_count: locations.reduce((sum, location) => sum + location.file_count, 0),
    locations,
    warnings: [],
    complete: true,
  };
}

async function simulatedRequest(path, options = {}) {
  await new Promise((resolve) => setTimeout(resolve, 160));
  const body = options.body ? JSON.parse(options.body) : {};
  const built = rebuildPreview();
  let payload;
  let status = 200;
  if (path.endsWith('/inventory')) {
    const roots = [
      ...new Map(
        samples.map((model) => [model.root, { kind: model.kind, path: model.root }]),
      ).values(),
    ];
    payload = {
      models: previewState.models,
      roots,
      warnings: [],
      settings: previewState.settings,
      workflow_scan: {
        workflows: built.workflows,
        directories: ['D:\\AI\\ComfyUI\\user\\default\\workflows'],
      },
      copies: built.copies,
      trash: previewTrash(),
    };
  } else if (path.endsWith('/settings')) {
    if (options.method === 'POST') {
      if (!(body.stale_days > 0) || body.stale_days > 36500) {
        status = 400;
        payload = { error: 'Choose a recency window between 0 and 36500 days.' };
      } else {
        previewState.settings = { ...previewState.settings, ...body };
        if (body.trash_directory)
          previewState.trashLocations.add(`${body.trash_directory.replace(/[\\/]+$/, '')}\\trash`);
        previewState.plans.clear();
      }
    }
    payload ||= previewState.settings;
  } else if (path.endsWith('/protection')) {
    const model = previewState.models.find((model) => model.id === body.model_id);
    model.protected = body.protected;
    previewState.plans.clear();
    payload = { model_id: model.id, protected: model.protected };
  } else if (path.endsWith('/duplicates/scan')) {
    const sizes = new Map();
    for (const model of previewState.models) {
      if (!sizes.has(model.size_bytes)) sizes.set(model.size_bytes, []);
      sizes.get(model.size_bytes).push(model);
    }
    let hashed_files = 0;
    let cached_files = 0;
    for (const group of sizes.values())
      if (group.length > 1)
        for (const model of group) {
          previewState.checked.has(model.id) ? cached_files++ : hashed_files++;
          previewState.checked.add(model.id);
        }
    payload = { ...rebuildPreview().copies, hashed_files, cached_files, warnings: [] };
  } else if (path.endsWith('/trash/summary')) payload = previewTrash();
  else if (path.endsWith('/trash/preview')) {
    const occupied = new Set(
      previewState.moves
        .filter((move) => move.status === 'trashed')
        .map((move) => move.destination.toLowerCase()),
    );
    const selected = body.model_ids.map((id) =>
      previewState.models.find((model) => model.id === id),
    );
    if (selected.some((model) => model.protected)) {
      status = 400;
      payload = { error: 'Protected models cannot be moved.' };
    } else {
      const files = selected.map((model) => {
        const trash = `${previewState.settings.trash_directory.replace(/[\\/]+$/, '')}\\trash\\`;
        let destination = trash + model.logical_path.replaceAll('/', '\\');
        let counter = 0;
        while (occupied.has(destination.toLowerCase()))
          destination = `${trash}collisions\\${model.id}-${++counter}\\${model.logical_path.replaceAll('/', '\\')}`;
        occupied.add(destination.toLowerCase());
        return {
          id: model.id,
          source: model.path,
          destination,
          size_bytes: model.size_bytes,
          workflows: model.workflows,
          copies: model.copies,
        };
      });
      const plan_id = `preview-plan-${Date.now()}`;
      previewState.plans.set(plan_id, files);
      payload = { plan_id, files };
    }
  } else if (path.endsWith('/trash')) {
    const files = previewState.plans.get(body.plan_id) || [];
    const results = files.map((file, index) => {
      const id = `preview-move-${Date.now()}-${index}`;
      const model = structuredClone(previewState.models.find((model) => model.id === file.id));
      previewState.moves.unshift({
        ...file,
        id,
        status: 'trashed',
        created_at: new Date().toISOString(),
        model,
      });
      previewState.models = previewState.models.filter((model) => model.id !== file.id);
      return { ...file, id, success: true };
    });
    previewState.plans.delete(body.plan_id);
    payload = { results };
  } else if (path.endsWith('/moves')) payload = { moves: previewState.moves };
  else if (path.endsWith('/restore')) {
    const move = previewState.moves.find((move) => move.id === body.move_id);
    previewState.models.push(structuredClone(move.model));
    move.status = 'restored';
    payload = { success: true };
  } else {
    status = 404;
    payload = { error: 'Preview route unavailable' };
  }
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
const api = { fetchApi: simulatedRequest };
const app = {
  extensionManager: {
    registerSidebarTab(tab) {
      tab.render(document.querySelector('#preview-launcher'));
    },
  },
  registerExtension(extension) {
    extension.setup();
  },
};
