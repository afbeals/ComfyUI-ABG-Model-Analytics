// @ts-check
import { app } from '../../../scripts/app.js';
import { api } from '../../../scripts/api.js';

const PREFIX = '/abg-model-analytics';
const escape = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char],
  );
const date = (value) => (value ? new Date(value).toLocaleString() : 'No recorded history');
const age = (value) => {
  if (!value) return 'No recorded activity';
  const days = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 86400000));
  return days === 0 ? 'Today' : days === 1 ? 'Yesterday' : `${days} days ago`;
};

function bytes(value) {
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index++;
  }
  return `${value.toFixed(index ? 1 : 0)} ${units[index]}`;
}

async function request(path, body) {
  const response = await api.fetchApi(
    PREFIX + path,
    body === undefined
      ? {}
      : {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        },
  );
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}

class Dashboard {
  constructor() {
    this.selected = new Set();
    this.tab = 'stale';
    this.search = '';
    this.kind = '';
    this.root = '';
    this.sort = 'size';
    this.workflow = '';
    this.busy = false;
    this.hashing = false;
  }

  open() {
    if (this.dialog?.isConnected) {
      this.dialog.focus();
      return;
    }
    if (!document.querySelector('link[data-abg-analytics]')) {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = new URL('../css/style.css', import.meta.url).href;
      link.dataset.abgAnalytics = 'true';
      document.head.append(link);
    }
    this.selected.clear();
    const dialog = document.createElement('dialog');
    dialog.className = 'abg-analytics';
    dialog.setAttribute('aria-labelledby', 'abg-title');
    dialog.innerHTML = `
      <header><div><span class="abg-eyebrow">YOUR COMFYUI LIBRARY</span><h2 id="abg-title">ABG Model Analytics</h2><p>Review model activity and make room with reversible cleanup.</p></div><div class="abg-header-actions"><button data-action="refresh">Rescan library</button><button data-action="close" aria-label="Close model analytics">Close</button></div></header>
      <details class="abg-settings-panel"><summary><span>Cleanup settings</span><span class="abg-settings-summary">Choose a trash directory to get started</span></summary><form class="abg-settings">
        <label class="abg-directory">Trash parent directory <input name="trash_directory" type="text" placeholder="D:\\ComfyCleanup" autocomplete="off" spellcheck="false" aria-describedby="abg-trash-help"></label>
        <label>Recent within (days) <input name="stale_days" type="number" min="0.01" max="36500" step="any" required></label>
        <button type="submit" class="abg-primary">Save settings</button>
        <p id="abg-trash-help">Choose a directory on your ComfyUI computer. Files go into its trash/models/… subdirectory.</p>
      </form></details>
      <div class="abg-message" role="status" aria-live="polite"></div>
      <div class="abg-overview" aria-label="Library overview"></div>
      <nav aria-label="Model views" class="abg-tabs"></nav>
      <p class="abg-history-note"></p>
      <div class="abg-filters">
        <label>Search <input type="search" name="search" placeholder="Model name or path"></label>
        <label>Category <select name="kind" aria-label="Category"><option value="">All categories</option></select></label>
        <label>Source directory <select name="root" aria-label="Source directory"><option value="">All directories</option></select></label>
        <label>Saved workflow <select name="workflow" aria-label="Saved workflow"><option value="">All saved workflows</option></select></label>
        <label>Sort <select name="sort" aria-label="Sort"><option value="size">Largest first</option><option value="oldest">Oldest use first</option><option value="newest">Newest use first</option><option value="name">Name</option></select></label>
      </div>
      <div class="abg-content" aria-busy="true">Loading inventory…</div>
      <footer class="abg-selection"><div><strong class="abg-selection-count">No models selected</strong><small class="abg-selection-help">Select files to preview their trash destinations.</small></div><div class="abg-selection-actions"><button data-action="clear" disabled>Clear selection</button><button data-action="preview" class="abg-primary" type="button" disabled>Preview move (0)</button></div></footer>`;
    document.body.append(dialog);
    this.dialog = dialog;
    dialog.querySelector('[data-action="close"]').onclick = () => dialog.close();
    dialog.addEventListener('cancel', (event) => {
      if (this.busy) event.preventDefault();
    });
    dialog.addEventListener('close', () => {
      dialog.remove();
      if (this.dialog === dialog) this.dialog = null;
    });
    dialog.querySelector('[data-action="refresh"]').onclick = () => this.refresh();
    dialog.querySelector('[data-action="preview"]').onclick = () => this.preview();
    dialog.querySelector('[data-action="clear"]').onclick = () => {
      this.selected.clear();
      this.render();
    };
    dialog.querySelector('form').onsubmit = async (event) => {
      event.preventDefault();
      if (this.busy) return;
      const form = event.currentTarget;
      this.setBusy(true);
      try {
        await request('/settings', {
          trash_directory: form.elements.trash_directory.value,
          stale_days: Number(form.elements.stale_days.value),
        });
        this.message('Settings saved.');
        await this.refresh();
        dialog.querySelector('.abg-settings-panel').open = false;
      } catch (error) {
        this.message(error.message, true);
      } finally {
        this.setBusy(false);
      }
    };
    for (const field of ['search', 'kind', 'root', 'workflow', 'sort']) {
      const input = dialog.querySelector(`[name="${field}"]`);
      input.addEventListener(field === 'search' ? 'input' : 'change', () => {
        this[field] = input.value;
        this.render();
      });
      input.value = this[field];
    }
    dialog.showModal();
    this.refresh();
  }

  message(text, error = false) {
    const element = this.dialog?.querySelector('.abg-message');
    if (element) {
      element.textContent = text;
      element.classList.toggle('abg-error', error);
    }
  }

  setBusy(value) {
    this.busy = value;
    this.dialog
      ?.querySelectorAll('.abg-settings input, .abg-settings button, [data-action=close]')
      .forEach((element) => {
        element.disabled = value;
      });
    this.updateSelection();
  }

  async refresh() {
    if (!this.dialog) return;
    const dialog = this.dialog;
    const content = dialog.querySelector('.abg-content');
    content.setAttribute('aria-busy', 'true');
    try {
      const data = await request('/inventory');
      if (this.dialog !== dialog) return;
      this.data = data;
      this.selected = new Set(
        [...this.selected].filter((id) =>
          data.models.some((model) => model.id === id && model.can_move),
        ),
      );
      const form = dialog.querySelector('form');
      form.elements.trash_directory.value = data.settings.trash_directory;
      form.elements.stale_days.value = data.settings.stale_days;
      const settingsPanel = dialog.querySelector('.abg-settings-panel');
      if (!settingsPanel.dataset.initialized) {
        settingsPanel.open = !data.settings.trash_directory;
        settingsPanel.dataset.initialized = 'true';
      }
      dialog.querySelector('.abg-settings-summary').textContent = data.settings.trash_directory
        ? `${data.settings.trash_directory} · ${data.settings.stale_days}-day recency window`
        : 'Choose a trash directory to enable moves';
      dialog.querySelector('.abg-history-note').textContent =
        `Tracking since ${date(data.settings.tracking_since)}. Unknown history includes activity from before tracking began. Recency reflects successful loads and successful workflow references.`;
      for (const [field, values] of [
        ['kind', [...new Set(data.models.flatMap((model) => model.kinds))]],
        ['root', [...new Set(data.models.map((model) => model.root))]],
        ['workflow', (data.workflow_scan?.workflows || []).map((workflow) => workflow.name)],
      ]) {
        const select = dialog.querySelector(`[name="${field}"]`);
        select.innerHTML = `<option value="">All ${field === 'kind' ? 'categories' : field === 'workflow' ? 'saved workflows' : 'directories'}</option>${values
          .sort()
          .map((value) => `<option value="${escape(value)}">${escape(value)}</option>`)
          .join('')}`;
        if (!values.includes(this[field])) this[field] = '';
        select.value = this[field];
      }
      await this.render();
    } catch (error) {
      this.message(error.message, true);
    } finally {
      content.setAttribute('aria-busy', 'false');
    }
  }

  filteredModels() {
    if (!this.data) return [];
    const search = this.search.toLowerCase();
    return this.data.models
      .filter(
        (model) =>
          this.matchesTab(model) &&
          (!this.kind || model.kinds.includes(this.kind)) &&
          (!this.root || model.root === this.root) &&
          (!this.workflow ||
            (model.workflows || []).some((workflow) => workflow.name === this.workflow)) &&
          `${model.logical_path} ${model.path}`.toLowerCase().includes(search),
      )
      .sort((a, b) => {
        if (this.sort === 'name') return a.logical_path.localeCompare(b.logical_path);
        if (this.sort === 'oldest') return (a.last_used || '').localeCompare(b.last_used || '');
        if (this.sort === 'newest') return (b.last_used || '').localeCompare(a.last_used || '');
        return b.size_bytes - a.size_bytes;
      });
  }

  matchesTab(model, tab = this.tab) {
    if (tab === 'all') return true;
    if (tab === 'protected') return model.protected;
    if (tab === 'copies') return model.copies?.length || model.duplicate_paths?.length;
    return model.status === tab;
  }

  workflowList(references = []) {
    return references.length
      ? `<ul class="abg-reference-list">${references.map((reference) => `<li><span>${escape(reference.name)}</span><small>${escape({ resolved: 'Resolves to this file', possible: 'Possible reference — custom loader', fallback: 'Fallback copy — another file currently resolves' }[reference.match] || reference.match)}</small></li>`).join('')}</ul>`
      : '';
  }

  modelDetails(model) {
    const workflows = model.workflows || [];
    const copies = model.copies || [];
    const identical = model.duplicate_paths || [];
    const labels = [];
    if (workflows.length)
      labels.push(`${workflows.length} saved ${workflows.length === 1 ? 'workflow' : 'workflows'}`);
    if (copies.length)
      labels.push(
        model.shadowed
          ? 'Shadowed copy'
          : copies.some((copy) => copy.role === 'active')
            ? 'Active copy · same-name files'
            : 'Same-name files',
      );
    if (identical.length) labels.push(`Identical content · ${identical.length + 1} files`);
    if (!labels.length) return '';
    return `<details class="abg-model-details"><summary>${escape(labels.join(' · '))}</summary><div>${this.workflowList(workflows)}${copies.map((copy) => `<p><strong>${escape(copy.kind)}: ${escape(copy.role)}</strong><small>ComfyUI currently resolves: ${escape(copy.active_path || 'Not resolved')}</small>${copy.other_paths.map((path) => `<small>Other location: ${escape(path)}</small>`).join('')}</p>`).join('')}${identical.length ? `<p><strong>Identical content verified with SHA-256</strong>${identical.map((path) => `<small>${escape(path)}</small>`).join('')}</p>` : copies.length ? '<p class="abg-detail-note">Matching names do not establish matching contents. Run an exact duplicate check to compare files.</p>' : ''}</div></details>`;
  }

  async setProtection(model) {
    if (this.busy) return;
    this.setBusy(true);
    try {
      await request('/protection', { model_id: model.id, protected: !model.protected });
      await this.refresh();
      this.message(
        model.protected ? 'Protection removed.' : 'Model protected. It cannot be moved to trash.',
      );
    } catch (error) {
      this.message(error.message, true);
    } finally {
      this.setBusy(false);
    }
  }

  async checkDuplicates() {
    if (this.hashing) return;
    this.hashing = true;
    this.message(
      'Checking file contents for exact duplicates. Large model files can take a while; you can keep browsing.',
    );
    this.render();
    try {
      const result = await request('/duplicates/scan', {});
      await this.refresh();
      this.message(
        `Exact duplicate check complete: ${result.verified_groups} identical-content groups. ${result.hashed_files} files checked, ${result.cached_files} cached.${result.warnings.length ? ` ${result.warnings.join('; ')}` : ''}`,
        result.warnings.length > 0,
      );
    } catch (error) {
      this.message(error.message, true);
    } finally {
      this.hashing = false;
      this.render();
    }
  }

  trashOverview() {
    const trash = this.data.trash || {
      size_bytes: 0,
      file_count: 0,
      locations: [],
      warnings: [],
      complete: true,
    };
    return `<div class="abg-trash-heading"><strong>${bytes(trash.size_bytes)} in trash</strong><span>${trash.file_count} files across ${trash.locations.length} locations</span></div><p class="abg-detail-note">Totals come from files on disk, including previous trash locations. Moving within one drive does not free its space. Trash is emptied manually.</p>${!trash.complete ? '<p class="abg-callout">Some locations are unavailable or contain skipped links. The total includes only files that could be scanned.</p>' : ''}${trash.locations.length ? `<details class="abg-trash-locations"><summary>Trash locations</summary><table><thead><tr><th>Directory</th><th>Size</th><th>Files</th></tr></thead><tbody>${trash.locations.map((location) => `<tr><td class="abg-path">${escape(location.path)}${location.current ? '<small>Current destination</small>' : '<small>Previous destination</small>'}${location.note ? `<small>${escape(location.note)}</small>` : ''}</td><td>${bytes(location.size_bytes)}</td><td>${location.file_count}</td></tr>`).join('')}</tbody></table></details>` : '<p>Configure a trash directory in Cleanup settings.</p>'}${trash.warnings.length ? `<details><summary>Trash scan notes</summary><ul>${trash.warnings.map((warning) => `<li>${escape(warning)}</li>`).join('')}</ul></details>` : ''}`;
  }

  async render() {
    if (!this.dialog || !this.data) return;
    const dialog = this.dialog;
    this.updateSelection();
    dialog.querySelector('.abg-selection').hidden = ['roots', 'moves', 'workflows'].includes(
      this.tab,
    );
    const navigation = dialog.querySelector('.abg-tabs');
    const overview = dialog.querySelector('.abg-overview');
    overview.innerHTML = `${[
      [
        'stale',
        'Not used recently',
        `No recorded activity in ${this.data.settings.stale_days} days`,
      ],
      ['unknown', 'Unknown history', 'Review separately · no usage evidence yet'],
      ['recent', 'Recently used', `Active within ${this.data.settings.stale_days} days`],
    ]
      .map(([status, label, detail]) => {
        const models = this.data.models.filter((model) => model.status === status);
        return `<button class="abg-stat abg-stat-${status}" data-status="${status}" aria-label="View ${label.toLowerCase()} models"><span>${escape(label)}</span><strong>${bytes(models.reduce((total, model) => total + model.size_bytes, 0))}</strong><small>${models.length} ${models.length === 1 ? 'model' : 'models'} · ${escape(detail)}</small></button>`;
      })
      .join(
        '',
      )}<button class="abg-stat abg-stat-trash" data-status="moves" aria-label="View trash and move history"><span>Trash</span><strong>${bytes(this.data.trash?.size_bytes || 0)}</strong><small>${this.data.trash?.file_count || 0} files · current and previous trash locations</small></button>`;
    overview.querySelectorAll('[data-status]').forEach((button) => {
      button.onclick = () => {
        this.tab = button.dataset.status;
        this.render();
      };
    });
    const tabs = [
      ['stale', 'Not recently used'],
      ['unknown', 'Unknown history'],
      ['recent', 'Recently used'],
      ['all', 'All models'],
      ['protected', 'Protected'],
      ['copies', 'Copies'],
      ['workflows', 'Saved workflows'],
      ['roots', 'Directories'],
      ['moves', 'Trash & history'],
    ];
    navigation.innerHTML = tabs
      .map(
        ([id, label]) =>
          `<button type="button" data-tab="${id}" aria-pressed="${this.tab === id}">${label}${['roots', 'moves'].includes(id) ? '' : ` (${id === 'workflows' ? (this.data.workflow_scan?.workflows || []).length : this.data.models.filter((model) => this.matchesTab(model, id)).length})`}</button>`,
      )
      .join('');
    navigation.querySelectorAll('button').forEach((button) => {
      button.onclick = () => {
        this.tab = button.dataset.tab;
        this.render();
      };
    });
    dialog.querySelector('.abg-filters').hidden = ['roots', 'moves', 'workflows'].includes(
      this.tab,
    );
    const content = dialog.querySelector('.abg-content');
    if (this.tab === 'workflows') {
      const workflows = this.data.workflow_scan?.workflows || [];
      content.innerHTML = `<p class="abg-detail-note">Saved references help you review cleanup impact. They do not count as recent activity. Known loaders identify the active file; custom loaders are marked as possible references.</p>${workflows.length ? `<div class="abg-scroll"><table><caption>Saved workflow references</caption><thead><tr><th>Workflow</th><th>Active file references</th><th>Possible / fallback references</th><th>Action</th></tr></thead><tbody>${workflows.map((workflow, index) => `<tr><td class="abg-path"><strong>${escape(workflow.name)}</strong><small>${escape(workflow.path)}</small></td><td>${workflow.models.filter((model) => model.match === 'resolved').length}</td><td>${workflow.models.filter((model) => model.match !== 'resolved').length}</td><td><button data-workflow-index="${index}" aria-label="View models referenced by ${escape(workflow.name)}">View models</button></td></tr>`).join('')}</tbody></table></div>` : '<div class="abg-empty"><strong>No saved workflows found.</strong><p>Save a workflow in ComfyUI, then rescan the library.</p></div>'}`;
      content.querySelectorAll('[data-workflow-index]').forEach((button) => {
        button.onclick = () => {
          this.workflow = workflows[Number(button.dataset.workflowIndex)].name;
          this.kind = '';
          this.root = '';
          this.search = '';
          this.tab = 'all';
          for (const field of ['workflow', 'kind', 'root', 'search'])
            dialog.querySelector(`[name="${field}"]`).value = this[field];
          this.render();
        };
      });
      return;
    }
    if (this.tab === 'moves') {
      content.textContent = 'Loading move history…';
      try {
        const { moves } = await request('/moves');
        if (this.dialog !== dialog || this.tab !== 'moves') return;
        content.innerHTML = `${this.trashOverview()}<p class="abg-detail-note">Restore returns a file to its original path without overwriting existing files. Interrupted operations marked pending or restoring may need manual recovery.</p>${moves.length ? `<div class="abg-scroll"><table><caption>Moves and restores</caption><thead><tr><th>Date</th><th>Original path</th><th>Trash path</th><th>Status</th><th>Action</th></tr></thead><tbody>${moves.map((move) => `<tr><td>${escape(date(move.created_at))}</td><td class="abg-path">${escape(move.source)}</td><td class="abg-path">${escape(move.destination)}</td><td>${escape(move.status)}${move.error ? `<p class="abg-error">${escape(move.error)}</p>` : ''}</td><td>${move.status === 'trashed' ? `<button data-restore="${escape(move.id)}" aria-label="Restore ${escape(move.source)}">Restore</button>` : ''}</td></tr>`).join('')}</tbody></table></div>` : '<p>No files have been moved.</p>'}`;
        content.querySelectorAll('[data-restore]').forEach((button) => {
          button.onclick = () => this.confirmRestore(button.dataset.restore, moves);
        });
      } catch (error) {
        this.message(error.message, true);
      }
      return;
    }
    if (this.tab === 'roots') {
      content.innerHTML = `<p>These paths come from ComfyUI’s live folder registry, including extra_model_paths.yaml and Desktop’s configured paths. To add model locations, configure them in ComfyUI and restart.</p><div class="abg-scroll"><table><caption>Configured model directories</caption><thead><tr><th>Category</th><th>Directory</th></tr></thead><tbody>${this.data.roots.map((root) => `<tr><td>${escape(root.kind)}</td><td class="abg-path">${escape(root.path)}</td></tr>`).join('')}</tbody></table></div>`;
      if (this.data.warnings.length)
        content.innerHTML += `<details open><summary>Scan notes (${this.data.warnings.length})</summary><ul>${this.data.warnings.map((warning) => `<li>${escape(warning)}</li>`).join('')}</ul></details>`;
      return;
    }
    const models = this.filteredModels();
    this.visible = models;
    content.innerHTML = `<p class="abg-results-summary">${models.length} files · ${bytes(models.reduce((total, model) => total + model.size_bytes, 0))}${this.tab === 'stale' ? ` · Last recorded use more than ${escape(this.data.settings.stale_days)} days ago` : ''}${this.data.warnings.length ? ` · ${this.data.warnings.length} scan notes in Directories` : ''}</p>${this.tab === 'copies' ? `<div class="abg-copy-actions"><p>Same-name files may have different contents. Shadowed files are hidden by a higher-priority location in ComfyUI. Exact checks compare same-size files with SHA-256 and cache the results.</p><button data-check-duplicates ${this.hashing ? 'disabled' : ''}>${this.hashing ? 'Checking contents…' : 'Check exact duplicates'}</button></div>` : ''}${this.tab === 'unknown' ? '<p class="abg-callout">No recorded history is not the same as never used. These files may have been used before tracking began or by a loader we cannot observe.</p>' : ''}${models.length ? `<div class="abg-scroll"><table><caption class="abg-sr-only">Model inventory</caption><thead><tr><th><input type="checkbox" data-select-all aria-label="Select all movable files in this view"></th><th>Model / original path</th><th>Category</th><th>Size</th><th>Last activity</th><th>Status</th><th>Protection</th></tr></thead><tbody>${models.map((model) => `<tr><td><input type="checkbox" data-id="${model.id}" ${this.selected.has(model.id) ? 'checked' : ''} ${model.can_move ? '' : 'disabled'} aria-label="Select ${escape(model.path)}"></td><td class="abg-path"><strong>${escape(model.logical_path)}</strong><small>${escape(model.path)}</small>${model.disabled ? '<small>Disabled file</small>' : ''}${model.move_reason ? `<small>${escape(model.move_reason)}</small>` : ''}${this.modelDetails(model)}</td><td><span class="abg-category">${escape(model.kinds.join(', '))}</span></td><td class="abg-nowrap">${bytes(model.size_bytes)}</td><td title="${escape(`Loaded: ${date(model.last_loaded)}; Workflow reference: ${date(model.last_referenced)}`)}">${escape(age(model.last_used))}<small>${model.last_used ? escape(date(model.last_used)) : 'Tracking will update after use'}</small></td><td><span class="abg-badge abg-badge-${model.status}">${escape({ stale: 'Not recent', recent: 'Recent', unknown: 'Unknown' }[model.status])}</span></td><td><button class="abg-protect" data-protect="${model.id}" aria-pressed="${Boolean(model.protected)}" aria-label="${model.protected ? 'Unprotect' : 'Protect'} ${escape(model.path)}">${model.protected ? 'Protected' : 'Protect'}</button></td></tr>`).join('')}</tbody></table></div>` : '<div class="abg-empty"><strong>No models match this view.</strong><p>Try another view or adjust your search and filters.</p></div>'}`;
    content.querySelectorAll('[data-protect]').forEach((button) => {
      button.onclick = () =>
        this.setProtection(this.data.models.find((model) => model.id === button.dataset.protect));
    });
    content.querySelectorAll('[data-id]').forEach((input) => {
      input.onchange = () => {
        input.checked
          ? this.selected.add(input.dataset.id)
          : this.selected.delete(input.dataset.id);
        this.updateSelection();
      };
    });
    const checkButton = content.querySelector('[data-check-duplicates]');
    if (checkButton) checkButton.onclick = () => this.checkDuplicates();
    const all = content.querySelector('[data-select-all]');
    if (all)
      all.onchange = () => {
        models
          .filter((model) => model.can_move)
          .forEach((model) => {
            all.checked ? this.selected.add(model.id) : this.selected.delete(model.id);
          });
        content.querySelectorAll('[data-id]:not(:disabled)').forEach((input) => {
          input.checked = all.checked;
        });
        this.updateSelection();
      };
    this.updateSelection();
  }

  updateSelection() {
    const button = this.dialog?.querySelector('[data-action="preview"]');
    if (!button) return;
    button.textContent = `Preview move (${this.selected.size})`;
    button.disabled = this.busy || !this.selected.size || !this.data?.settings.trash_directory;
    const selected = (this.data?.models || []).filter((model) => this.selected.has(model.id));
    this.dialog.querySelector('.abg-selection-count').textContent = selected.length
      ? `${selected.length} ${selected.length === 1 ? 'model' : 'models'} selected · ${bytes(selected.reduce((total, model) => total + model.size_bytes, 0))}`
      : 'No models selected';
    this.dialog.querySelector('.abg-selection-help').textContent = !this.data?.settings
      .trash_directory
      ? 'Set a trash directory in Cleanup settings to enable moves.'
      : selected.length
        ? 'Review the exact destinations before any files move.'
        : 'Select files to preview their trash destinations.';
    this.dialog.querySelector('[data-action="clear"]').disabled = this.busy || !this.selected.size;
    const all = this.dialog.querySelector('[data-select-all]');
    if (all) {
      const movable = (this.visible || []).filter((model) => model.can_move);
      const count = movable.filter((model) => this.selected.has(model.id)).length;
      all.checked = movable.length > 0 && count === movable.length;
      all.indeterminate = count > 0 && count < movable.length;
    }
  }

  confirm(title, description, rows, actionLabel, onConfirm) {
    const dialog = document.createElement('dialog');
    dialog.className = 'abg-analytics abg-confirm';
    dialog.setAttribute('aria-labelledby', 'abg-confirm-title');
    const affected = new Set(
      rows.flatMap((row) =>
        (row.workflows || [])
          .filter((reference) => reference.match !== 'fallback')
          .map((reference) => reference.name),
      ),
    );
    const workflowWarning = affected.size
      ? `<div class="abg-workflow-warning"><strong>Review saved workflow dependencies</strong><p>${affected.size} saved ${affected.size === 1 ? 'workflow references' : 'workflows reference'} this selection. Moving these files may make workflows fail or load a different copy.</p></div>`
      : '';
    dialog.innerHTML = `<span class="abg-eyebrow">REVIEW FILE LOCATIONS</span><h2 id="abg-confirm-title">${escape(title)}</h2><p>${escape(description)}</p>${workflowWarning}<div class="abg-scroll"><table><caption class="abg-sr-only">File move destinations</caption><thead><tr><th>From</th><th>To</th><th>Size</th></tr></thead><tbody>${rows.map((row) => `<tr><td class="abg-path">${escape(row.source)}${this.workflowList(row.workflows)}${(row.copies || []).some((copy) => copy.role === 'active') ? '<p class="abg-detail-note">A same-name file remains in another location and may become the active copy.</p>' : ''}</td><td class="abg-path abg-destination">${escape(row.destination)}</td><td>${bytes(row.size_bytes)}</td></tr>`).join('')}</tbody></table></div><p class="abg-error" role="alert"></p><footer><button data-cancel>Cancel</button><button class="abg-primary" data-confirm>${escape(actionLabel)}</button></footer>`;
    let submitting = false;
    document.body.append(dialog);
    dialog.querySelector('[data-cancel]').onclick = () => dialog.close();
    dialog.addEventListener('cancel', (event) => {
      if (submitting) event.preventDefault();
    });
    dialog.addEventListener('close', () => dialog.remove());
    dialog.querySelector('[data-confirm]').onclick = async () => {
      if (submitting) return;
      submitting = true;
      dialog.querySelectorAll('button').forEach((button) => {
        button.disabled = true;
      });
      dialog.querySelector('[data-confirm]').textContent = 'Moving files…';
      this.setBusy(true);
      try {
        await onConfirm();
        dialog.close();
      } catch (error) {
        dialog.querySelector('[role=alert]').textContent = error.message;
      } finally {
        submitting = false;
        this.setBusy(false);
        dialog.querySelectorAll('button').forEach((button) => {
          button.disabled = false;
        });
        dialog.querySelector('[data-confirm]').textContent = actionLabel;
      }
    };
    dialog.showModal();
    dialog.querySelector('[data-cancel]').focus();
  }

  async preview() {
    if (this.busy) return;
    this.setBusy(true);
    try {
      const plan = await request('/trash/preview', { model_ids: [...this.selected] });
      if (!this.dialog) return;
      this.confirm(
        'Review moves to trash',
        `${plan.files.length} ${plan.files.length === 1 ? 'file' : 'files'} · ${bytes(plan.files.reduce((total, row) => total + row.size_bytes, 0))}. Cross-drive moves copy files before removing originals and can take time. Restore is available in Trash & history.`,
        plan.files,
        'Move to trash',
        async () => {
          const { results } = await request('/trash', { plan_id: plan.plan_id });
          const failed = results.filter((result) => !result.success);
          this.selected.clear();
          this.tab = 'moves';
          await this.refresh();
          this.message(
            `${results.length - failed.length} ${results.length - failed.length === 1 ? 'file' : 'files'} moved.${failed.length ? ` ${failed.length} failed: ${failed.map((result) => `${result.source}: ${result.error}`).join('; ')}` : ''}`,
            failed.length > 0,
          );
        },
      );
    } catch (error) {
      this.message(error.message, true);
    } finally {
      this.setBusy(false);
    }
  }

  confirmRestore(id, moves) {
    if (this.busy) return;
    const move = moves.find((row) => row.id === id);
    this.confirm(
      'Restore model',
      'Return this file to its original location. An existing file at that location will be kept and the restore will stop.',
      [{ source: move.destination, destination: move.source, size_bytes: move.size_bytes }],
      'Restore file',
      async () => {
        await request('/restore', { move_id: id });
        await this.refresh();
        this.message('File restored to its original location.');
      },
    );
  }
}

const dashboard = new Dashboard();
app.registerExtension({
  name: 'ABG.ModelAnalytics',
  commands: [
    {
      id: 'ABG.ModelAnalytics.Open',
      label: 'ABG Model Analytics',
      function: () => dashboard.open(),
    },
  ],
  menuCommands: [{ path: ['extensions'], commands: ['ABG.ModelAnalytics.Open'] }],
  async setup() {
    if (typeof app.extensionManager?.registerSidebarTab === 'function') {
      app.extensionManager.registerSidebarTab({
        id: 'abg-model-analytics',
        icon: 'pi pi-chart-bar',
        title: 'ABG Model Analytics',
        tooltip: 'Model usage and cleanup',
        type: 'custom',
        render(element) {
          element.textContent = '';
          const button = document.createElement('button');
          button.textContent = 'Open ABG Model Analytics';
          button.style.margin = '16px';
          button.addEventListener('click', () => dashboard.open());
          element.append(button);
        },
      });
    } else {
      const button = document.createElement('button');
      button.textContent = 'ABG Model Analytics';
      button.className = 'comfy-menu-btn abg-launcher';
      button.onclick = () => dashboard.open();
      const menu =
        app.menu?.settingsGroup?.element?.parentElement || document.querySelector('.comfy-menu');
      if (menu) menu.append(button);
      else {
        button.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:9999';
        document.body.append(button);
      }
    }
  },
});
