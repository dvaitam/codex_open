const api = {
  async createRun({ repo_url, provider, model, task, api_key, truncate, truncate_limit }) {
    const res = await fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_url, provider, model, task, api_key, truncate, truncate_limit })
    });
    if (!res.ok) throw new Error('Failed to create run');
    return res.json();
  },
  async listRuns() {
    const res = await fetch('/api/runs');
    return res.json();
  },
  async getRun(runId) {
    const res = await fetch(`/api/run/${encodeURIComponent(runId)}`);
    if (!res.ok) throw new Error('Run not found');
    return res.json();
  },
  async getEvents(runId, pos = 0, limit = 200) {
    const res = await fetch(`/api/run/${encodeURIComponent(runId)}/events?pos=${pos}&limit=${limit}`);
    return res.json();
  }
};

function $(sel, el = document) { return el.querySelector(sel); }
function el(tag, attrs = {}, children = []) {
  const e = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => {
    if (k === 'class') e.className = v; else if (k === 'html') e.innerHTML = v; else e.setAttribute(k, v);
  });
  (Array.isArray(children) ? children : [children]).forEach(c => {
    if (c === null || c === undefined) return;
    if (typeof c === 'string' || typeof c === 'number') {
      e.appendChild(document.createTextNode(String(c)));
    } else {
      e.appendChild(c);
    }
  });
  return e;
}

function viewNewTask() {
  const container = el('div', { class: 'panel' }, [
    el('h2', { html: 'New Task' }),
    el('div', { class: 'row' }, [
      el('div', { class: 'col' }, [
        field('Repo URL', el('input', { id: 'repo_url', placeholder: 'https://github.com/user/repo.git or git@github.com:user/repo.git' })),
        field('Provider', providerChips()),
        apiKeyField(),
        modelField(),
        truncateField(),
      ]),
      el('div', { class: 'col' }, [
        field('Task', el('textarea', { id: 'task', placeholder: 'Describe what to do...' })),
      ])
    ]),
    el('div', { class: 'toolbar' }, [
      el('button', { class: 'btn', id: 'start' }, [document.createTextNode('Start Run')]),
      el('span', { class: 'hint' }, [document.createTextNode('Outputs stream live; you can also watch later from Runs.')])
    ])
  ]);

  function field(label, input) {
    return el('div', { class: 'field' }, [
      el('label', { html: label }),
      input
    ]);
  }
  function providerChips() {
    const wrap = el('div', { class: 'chip-group', id: 'providers' });
    const providers = [
      { key: 'simple', label: 'Simple', dot: 'simple' },
      { key: 'openai', label: 'OpenAI', dot: 'openai' },
      { key: 'claude', label: 'Claude', dot: 'claude' },
      { key: 'gemini', label: 'Gemini', dot: 'gemini' },
      { key: 'xai', label: 'xAI', dot: 'xai' },
    ];
    const savedProvider = localStorage.getItem('agent_async/provider') || 'simple';
    providers.forEach((p) => wrap.appendChild(chip(p.key, p.label, p.dot, p.key === savedProvider)));
    wrap.addEventListener('click', async (e) => {
      const target = e.target.closest('.chip');
      if (!target) return;
      // Auto-fetch models when provider changes and API key exists
      const prov = target.getAttribute('data-value');
      localStorage.setItem('agent_async/provider', prov);
      const input = $('#api_key', container);
      const currentKey = (input ? input.value : '').trim();
      const savedKey = localStorage.getItem(`agent_async/api_key/${prov}`) || '';
      // If there is a saved key for this provider, prefer it. Otherwise, keep current typed key.
      if (input) {
        if (savedKey) {
          input.value = savedKey;
        } else if (currentKey) {
          // Preserve user-typed key and save it for this provider
          localStorage.setItem(`agent_async/api_key/${prov}`, currentKey);
          input.value = currentKey;
        } // else leave empty
      }
      const apiKey = (input ? input.value : '').trim();
      await fetchModels(prov, apiKey);
    });
    return wrap;
  }
  function chip(value, label, dotClass, active = false) {
    const c = el('label', { class: `chip${active ? ' active' : ''}`, 'data-value': value }, [
      el('span', { class: `dot ${dotClass}` }),
      document.createTextNode(label)
    ]);
    c.addEventListener('click', () => {
      Array.from(container.querySelectorAll('.chip')).forEach(x => x.classList.remove('active'));
      c.classList.add('active');
    });
    return c;
  }

  function apiKeyField() {
    const row = el('div', { class: 'field' });
    row.appendChild(el('label', { html: 'API Key (stored in this browser)' }));
    const box = el('div', { class: 'row' }, [
      el('div', { class: 'col' }, el('input', { id: 'api_key', type: 'password', placeholder: 'Paste API key for selected provider' })),
      el('div', {}, el('button', { class: 'btn secondary', id: 'fetch_models' }, [document.createTextNode('Fetch Models')]))
    ]);
    row.appendChild(box);
    // SSH Key management UI
    const ssh = el('div', { class: 'panel' }, [
      el('h3', { html: 'SSH Key for Git (server-side)' }),
      el('div', { class: 'field' }, [ el('label', { html: 'Private Key' }), el('textarea', { id: 'ssh_key', placeholder: 'Paste your SSH private key (e.g., ed25519) here' }) ]),
      el('div', { class: 'toolbar' }, [
        el('button', { class: 'btn secondary', id: 'save_ssh' }, 'Save SSH Key'),
        el('button', { class: 'btn danger', id: 'delete_ssh' }, 'Remove SSH Key'),
        el('span', { id: 'ssh_status', class: 'hint' }, 'Not checked')
      ])
    ]);
    row.appendChild(ssh);
    row.addEventListener('click', async (e) => {
      if (e.target && e.target.id === 'fetch_models') {
        const activeChip = container.querySelector('.chip.active');
        const provider = activeChip ? activeChip.getAttribute('data-value') : 'simple';
        const apiKey = $('#api_key', container).value.trim();
        // Persist the key per provider in localStorage
        if (provider) localStorage.setItem(`agent_async/api_key/${provider}`, apiKey);
        await fetchModels(provider, apiKey, true);
      } else if (e.target && e.target.id === 'save_ssh') {
        const key = $('#ssh_key', row).value;
        try {
          const res = await fetch('/api/ssh-key', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ private_key: key }) });
          const data = await res.json();
          if (!res.ok || data.error) throw new Error(data.error || 'Failed to save');
          $('#ssh_status', row).textContent = 'SSH key saved on server';
        } catch (err) {
          $('#ssh_status', row).textContent = `Error: ${err.message}`;
        }
      } else if (e.target && e.target.id === 'delete_ssh') {
        try {
          const res = await fetch('/api/ssh-key', { method: 'DELETE' });
          const data = await res.json();
          if (!res.ok || data.error) throw new Error(data.error || 'Failed to delete');
          $('#ssh_status', row).textContent = 'SSH key removed';
        } catch (err) {
          $('#ssh_status', row).textContent = `Error: ${err.message}`;
        }
      }
    });
    // Pre-fill from storage for saved provider
    setTimeout(() => {
      try {
        const activeChip = container.querySelector('.chip.active');
        const prov = activeChip ? activeChip.getAttribute('data-value') : 'simple';
        const savedKey = localStorage.getItem(`agent_async/api_key/${prov}`) || '';
        const input = $('#api_key', container);
        if (input && savedKey) input.value = savedKey;
        // Check server SSH key presence
        fetch('/api/ssh-key').then(r => r.json()).then(d => { const elStatus = $('#ssh_status', row); if (elStatus) elStatus.textContent = d.present ? 'SSH key present on server' : 'No SSH key on server'; }).catch(() => {});
      } catch {}
    }, 0);
    return row;
  }

  function modelField() {
    const wrap = el('div', { class: 'field' });
    wrap.appendChild(el('label', { html: 'Model' }));
    const select = el('select', { id: 'model_select' }, [el('option', { value: '', html: '— Select a model —' })]);
    const input = el('input', { id: 'model', placeholder: '(optional custom model name)' });
    select.addEventListener('change', () => { input.value = select.value; });
    wrap.appendChild(select);
    wrap.appendChild(input);
    wrap.appendChild(el('div', { class: 'hint' }, 'You can select from fetched models or type a custom one.'));
    return wrap;
  }

  async function fetchModels(provider, apiKey, showAlerts = false) {
    const btn = $('#fetch_models', container);
    if (btn) { btn.disabled = true; btn.textContent = 'Fetching...'; }
    try {
      const qs = new URLSearchParams({ provider });
      if (apiKey) qs.set('api_key', apiKey);
      if (provider === 'xai') qs.set('debug', '1');
      const res = await fetch(`/api/models?${qs}`);
      const data = await res.json();
      const models = data.models || [];
      const sel = $('#model_select', container);
      sel.innerHTML = '';
      sel.appendChild(el('option', { value: '', html: '— Select a model —' }));
      models.forEach(m => sel.appendChild(el('option', { value: m, html: m })));
      if (models.length) { sel.value = models[0]; $('#model', container).value = models[0]; }
      if (showAlerts && data.error) alert(data.error);
    } catch (err) {
      if (showAlerts) alert(err.message || 'Failed to fetch models');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Fetch Models'; }
    }
  }

  // Recent repos support
  const datalist = el('datalist', { id: 'repo_suggestions' });
  container.appendChild(datalist);
  $('#repo_url', container).setAttribute('list', 'repo_suggestions');

  function renderRecentRepos(repos) {
    // Fill datalist
    datalist.innerHTML = '';
    (repos || []).forEach(r => datalist.appendChild(el('option', { value: r.url })));
    // Render chips
    const boxId = 'recent_repos_box';
    let box = $('#' + boxId, container);
    if (!box) {
      box = el('div', { class: 'field', id: boxId });
      box.appendChild(el('label', { html: 'Recent Repos' }));
      box.appendChild(el('div', { class: 'chip-group', id: 'recent_repos' }));
      // Insert under the repo input field
      const firstCol = container.querySelector('.col');
      firstCol && firstCol.insertBefore(box, firstCol.children[1]);
    }
    const wrap = $('#recent_repos', container);
    wrap.innerHTML = '';
    (repos || []).slice(0, 8).forEach(r => {
      const chip = el('label', { class: 'chip', 'data-value': r.url }, [el('span', { class: 'dot openai' }), r.url]);
      chip.addEventListener('click', () => { $('#repo_url', container).value = r.url; });
      wrap.appendChild(chip);
    });
  }

  async function loadRepos() {
    try {
      const res = await fetch('/api/repos');
      const data = await res.json();
      renderRecentRepos(data.repos || []);
    } catch {}
  }

  function truncateField() {
    const wrap = el('div', { class: 'field' });
    wrap.appendChild(el('label', { html: 'Output to Model' }));
    const row = el('div', { class: 'row' }, [
      el('div', {}, el('label', { class: 'chip' }, [
        el('input', { type: 'checkbox', id: 'truncate_chk' }),
        el('span', { class: 'dot simple' }),
        ' Truncate output sent to model'
      ])),
      el('div', { class: 'col' }, el('input', { id: 'truncate_limit', type: 'number', min: '0', placeholder: 'chars (e.g., 4000)', disabled: 'true' }))
    ]);
    wrap.appendChild(row);
    // Enable/disable limit based on checkbox
    row.addEventListener('change', () => {
      const on = $('#truncate_chk', wrap).checked;
      const inp = $('#truncate_limit', wrap);
      inp.disabled = !on;
      if (on && !inp.value) inp.value = '4000';
    });
    return wrap;
  }

  container.addEventListener('click', async (e) => {
    if (e.target && e.target.id === 'start') {
      const repo_url = $('#repo_url', container).value.trim();
      const activeChip = container.querySelector('.chip.active');
      const provider = activeChip ? activeChip.getAttribute('data-value') : 'simple';
      const model = $('#model', container).value.trim();
      const api_key = $('#api_key', container).value.trim();
      // Persist chosen provider and its API key
      if (provider) localStorage.setItem('agent_async/provider', provider);
      if (provider) localStorage.setItem(`agent_async/api_key/${provider}`, api_key);
      const task = $('#task', container).value.trim();
      const truncate = $('#truncate_chk', container).checked;
      const truncate_limit = parseInt($('#truncate_limit', container).value || '4000', 10);
      if (!repo_url || !task) return alert('Repo URL and Task are required');
      try {
        const { run_id } = await api.createRun({ repo_url, provider, model, task, api_key, truncate, truncate_limit });
        // Save repo for future use (server also saves on run create)
        try { await fetch('/api/repos', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ repo_url }) }); } catch {}
        location.hash = `#run:${run_id}`;
      } catch (err) {
        alert(err.message || 'Failed to start run');
      }
    }
  });

  // Kick off initial model fetch for default provider and load repos
  setTimeout(() => {
    try {
      const activeChip = container.querySelector('.chip.active');
      const provider = activeChip ? activeChip.getAttribute('data-value') : (localStorage.getItem('agent_async/provider') || 'simple');
      const apiKey = $('#api_key', container).value.trim();
      fetchModels(provider, apiKey);
      loadRepos();
    } catch {}
  }, 0);

  return container;
}

// Initial render will fetch model list for default provider

function viewRuns() {
  const panel = el('div', { class: 'panel' }, [el('h2', { html: 'Runs' }), el('div', { id: 'runs' }, 'Loading...')]);
  api.listRuns().then(({ runs }) => {
    const tbl = el('table', { class: 'runs' });
    const thead = el('thead', {}, el('tr', {}, [
      el('th', { html: 'Run Id' }),
      el('th', { html: 'Provider/Model' }),
      el('th', { html: 'Task' }),
      el('th', { html: 'Repo' }),
    ]));
    const tbody = el('tbody');
    (runs || []).slice().reverse().forEach(m => {
      const rid = m.id;
      const row = el('tr', {}, [
        el('td', {}, el('a', { href: `#run:${rid}` }, [document.createTextNode(rid)])),
        el('td', { html: `<span class="badge">${m.provider}</span> <span class="badge strong">${m.model || ''}</span>` }),
        el('td', { html: m.task }),
        el('td', { html: m.repo_url || m.repo_path }),
      ]);
      tbody.appendChild(row);
    });
    tbl.appendChild(thead); tbl.appendChild(tbody);
    $('#runs', panel).innerHTML = '';
    $('#runs', panel).appendChild(tbl);
  });
  return panel;
}

function viewRun(runId) {
  const container = el('div', { class: 'panel' }, [
    el('div', { class: 'toolbar' }, [
      el('div', { class: 'badge mono' }, `Run ${runId}`),
      el('div', { id: 'meta', class: 'badge' }, 'Loading...'),
      el('div', { id: 'status', class: 'badge' }, [el('span', { class: 'spinner' }), document.createTextNode(' Running')]),
      el('a', { class: 'btn', id: 'view_pr', href: '#', target: '_blank', style: 'display:none' }, 'View PR'),
      el('button', { class: 'btn secondary', id: 'open_pr_panel' }, 'Create PR'),
      el('button', { class: 'btn danger', id: 'cancel_run' }, 'Cancel Run'),
      el('button', { class: 'btn danger', id: 'delete_run' }, 'Delete Run')
    ]),
    el('div', { class: 'terminal', id: 'term' })
  ]);

  // Local field helper (avoid dependency on viewNewTask's scope)
  function field(label, input) {
    return el('div', { class: 'field' }, [
      el('label', { html: label }),
      input
    ]);
  }

  api.getRun(runId).then(meta => {
    $('#meta', container).innerHTML = `<span class="badge">${meta.provider}</span> <span class="badge strong">${meta.model || ''}</span> — ${meta.task}`;
  }).catch(() => { $('#meta', container).textContent = 'Run not found'; });

  const term = $('#term', container);
  let pos = 0;
  let alive = true;
  let idleCount = 0;
  function appendLine(cls, txt) {
    const d = el('div', { class: `term-line ${cls}` });
    d.textContent = txt;
    term.appendChild(d);
    term.scrollTop = term.scrollHeight;
  }
  async function pump() {
    while (alive) {
      try {
        const { next_pos, events } = await api.getEvents(runId, pos, 500);
        const prev = pos;
        pos = next_pos || pos;
        (events || []).forEach(evt => {
          const t = evt.type; const d = evt.data || {};
          if (t === 'agent.command') appendLine('cmd', `$ ${d.cmd}`);
          else if (t === 'proc.stdout') appendLine('stdout', d.text || '');
          else if (t === 'proc.stderr') appendLine('stderr', d.text || '');
          else if (t === 'provider.reply') {
            const info = d.file ? `${d.file} (${d.bytes||0} bytes)` : `${d.bytes||0} bytes`;
            appendLine('thought', `[info] Saved provider reply: ${info}`);
            if (d.excerpt) appendLine('stdout', d.excerpt);
          }
          else if (t === 'pr.url') {
            const link = $('#view_pr', container);
            if (link) { link.href = d.url; link.style.display = ''; }
            appendLine('thought', `[info] PR: ${d.url}`);
          }
          else if (t === 'agent.message') appendLine('thought', `[${d.role}] ${d.content}`);
          else if (t === 'agent.error') { appendLine('stderr', `[error] ${d.error}`); enablePrButton(); }
          else if (t === 'agent.done') {
            appendLine('done', '[done] agent completed');
            $('#status', container).textContent = 'Done';
            const cancel = $('#cancel_run', container);
            if (cancel) { cancel.disabled = true; cancel.textContent = 'Finished'; }
            alive = false; enablePrButton(); showPrPanel();
          }
        });
        if (pos === prev && (!events || events.length === 0)) {
          idleCount++;
        } else {
          idleCount = 0;
        }
      } catch (e) { /* ignore transient */ }
      const delay = idleCount > 20 ? 2000 : 400; // backoff when idle
      await new Promise(r => setTimeout(r, delay));
    }
  }

  function showPrPanel() {
    if ($('#pr_panel', container)) return;
    const panel = el('div', { class: 'panel', id: 'pr_panel' }, [
      el('h3', { html: 'Create Pull Request' }),
      el('div', { class: 'row' }, [
        el('div', { class: 'col' }, [
          el('div', { class: 'field' }, [ el('label', { html: 'Branch' }), el('input', { id: 'pr_branch', value: suggestBranch() }) ]),
          el('div', { class: 'field' }, [ el('label', { html: 'Title' }), el('input', { id: 'pr_title', value: suggestTitle() }) ]),
        ]),
        el('div', { class: 'col' }, [
          el('div', { class: 'field' }, [ el('label', { html: 'Description' }), el('textarea', { id: 'pr_body', placeholder: 'Optional PR description' }) ]),
        ])
      ]),
      el('div', { class: 'toolbar' }, [
        el('button', { class: 'btn', id: 'create_pr' }, 'Create PR'),
        el('span', { class: 'hint' }, 'Requires git remote auth; uses gh if available else pushes branch.')
      ])
    ]);
    container.appendChild(panel);

    panel.addEventListener('click', async (e) => {
      if (e.target && e.target.id === 'create_pr') {
        const branch = $('#pr_branch', panel).value.trim();
        const title = $('#pr_title', panel).value.trim();
        const body = $('#pr_body', panel).value.trim();
        e.target.disabled = true; e.target.textContent = 'Creating...';
        try {
          const res = await fetch(`/api/run/${encodeURIComponent(runId)}/pr`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ branch, title, body })
          });
          if (!res.ok) throw new Error('Failed to start PR');
          appendLine('thought', '[info] PR workflow started — see stream above.');
        } catch (err) {
          appendLine('stderr', `[error] ${err.message || 'Failed to create PR'}`);
        } finally {
          e.target.disabled = false; e.target.textContent = 'Create PR';
        }
      }
    });
  }

  function enablePrButton() {
    const btn = $('#open_pr_panel', container);
    if (btn) btn.disabled = false;
  }

  // Wire the PR button to always be available to open the panel
  const prBtn = $('#open_pr_panel', container);
  if (prBtn) {
    prBtn.disabled = false; // allow manual PR even before done
    prBtn.addEventListener('click', () => showPrPanel());
  }
  const cancelBtn = $('#cancel_run', container);
  if (cancelBtn) {
    cancelBtn.addEventListener('click', async () => {
      cancelBtn.disabled = true; cancelBtn.textContent = 'Cancelling...';
      try {
        const res = await fetch(`/api/run/${encodeURIComponent(runId)}/cancel`, { method: 'POST' });
        if (!res.ok) throw new Error('Cancel failed');
        appendLine('thought', '[info] Cancellation requested.');
      } catch (e) {
        appendLine('stderr', `[error] ${e.message || 'Cancel failed'}`);
      } finally {
        cancelBtn.textContent = 'Cancel Run';
      }
    });
  }
  const deleteBtn = $('#delete_run', container);
  if (deleteBtn) {
    deleteBtn.addEventListener('click', async () => {
      if (!confirm('Delete this run and its cloned repo (if applicable)?')) return;
      deleteBtn.disabled = true; deleteBtn.textContent = 'Deleting...';
      try {
        const res = await fetch(`/api/run/${encodeURIComponent(runId)}`, { method: 'DELETE' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) throw new Error(data.error || 'Delete failed');
        appendLine('thought', '[info] Run deleted. Redirecting to runs list...');
        setTimeout(() => { location.hash = '#runs'; }, 600);
      } catch (e) {
        appendLine('stderr', `[error] ${e.message || 'Delete failed'}`);
      } finally {
        deleteBtn.textContent = 'Delete Run';
      }
    });
  }

  function suggestBranch() {
    const base = (document.title || 'change').toLowerCase();
    const taskHint = ($('#meta', container).textContent || '').toLowerCase();
    const s = (taskHint.match(/—\s*(.*)$/) || [,'change'])[1] || 'change';
    const slug = s.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'change';
    return `feat/${slug}`;
  }
  function suggestTitle() {
    const taskHint = ($('#meta', container).textContent || '').split('—').slice(1).join('—').trim();
    return taskHint ? `Agent: ${taskHint}` : 'Agent: Proposed changes';
  }
  pump();

  container._teardown = () => { alive = false; };
  return container;
}

function render() {
  const app = document.getElementById('app');
  const hash = location.hash || '#new';
  const prev = app.firstChild; if (prev && typeof prev._teardown === 'function') prev._teardown();
  app.innerHTML = '';
  if (hash.startsWith('#run:')) {
    const runId = hash.slice('#run:'.length);
    app.appendChild(viewRun(runId));
  } else if (hash === '#runs') {
    app.appendChild(viewRuns());
  } else {
    app.appendChild(viewNewTask());
  }
}

window.addEventListener('hashchange', render);
window.addEventListener('DOMContentLoaded', render);
