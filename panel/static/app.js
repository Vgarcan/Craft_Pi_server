// Craft Pi Server panel — progressive enhancement. Every page and form also works without JavaScript.
(() => {
  'use strict';
  document.documentElement.classList.add('js');  // muestra los controles que sólo funcionan con JS
  const csrf = () => document.body.dataset.csrf || '';
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const timers = [];
  const every = (ms, fn) => { fn(); timers.push(setInterval(() => { if (!document.hidden) fn(); }, ms)); };
  const clearTimers = () => { while (timers.length) clearInterval(timers.pop()); };
  const store = {  // sólo comodidades del visitante (historial de la consola); puede no existir
    get(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* sin almacenamiento */ } },
  };
  const getJSON = async (url) => { const r = await fetch(url, { credentials: 'same-origin' }); if (r.status === 401) location.href = '/login'; return r.json(); };

  // --- Parallax: las capas leen --scroll desde CSS -----------------------------------------
  let ticking = false;
  const onScroll = () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => { document.documentElement.style.setProperty('--scroll', String(Math.round(scrollY))); ticking = false; });
  };
  addEventListener('scroll', onScroll, { passive: true });

  // --- Navegación fluida: sustituye <main> sin recargar la página ---------------------------
  const sameOrigin = (a) => a.origin === location.origin;
  async function navigate(url, push = true) {
    const main = $('#main');
    main.classList.add('leaving');
    try {
      const response = await fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } });
      if (response.redirected && new URL(response.url).pathname === '/login') { location.href = '/login'; return; }
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      const next = $('#main', doc);
      if (!next) { location.href = url; return; }
      clearTimers();
      main.replaceWith(next);
      document.title = next.dataset.title || document.title;
      document.body.className = doc.body.className;
      const nav = $('.hotbar', doc);
      if (nav && $('.hotbar')) $('.hotbar').replaceWith(nav);
      const toasts = $('.toasts', doc);
      if (toasts) $('.toasts').replaceWith(toasts);
      if (push) history.pushState({}, '', url);
      scrollTo(0, 0);
      init(document);
    } catch {
      location.href = url;
    }
  }
  document.addEventListener('click', (event) => {
    const link = event.target.closest('a[data-nav]');
    if (!link || !sameOrigin(link) || event.metaKey || event.ctrlKey || event.shiftKey || event.button) return;
    event.preventDefault();
    navigate(link.href);
  });
  addEventListener('popstate', () => navigate(location.href, false));

  // --- Barra de objetos: teclas 1-9 y rueda del ratón, como en el juego --------------------
  const typing = () => ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName);
  document.addEventListener('keydown', (event) => {
    if (typing() || event.altKey || event.ctrlKey || event.metaKey || !/^[1-9]$/.test(event.key)) return;
    const slot = $(`.hotbar [data-key="${event.key}"]`);
    if (slot) { event.preventDefault(); slot.click(); }
  });
  document.addEventListener('wheel', (event) => {
    const bar = event.target.closest('.hotbar');
    if (!bar) return;
    event.preventDefault();
    const slots = $$('.slot', bar);
    const current = slots.findIndex((s) => s.classList.contains('active'));
    const next = slots[(current + (event.deltaY > 0 ? 1 : -1) + slots.length) % slots.length];
    $$('.slot', bar).forEach((s) => s.classList.remove('show-label'));
    (next.querySelector('a, button') || next).focus();
  }, { passive: false });

  // --- Avisos y tareas en segundo plano -------------------------------------------------
  function toast(kind, title, text, onClick) {
    let box = $('.toasts');
    if (!box) { box = document.createElement('div'); box.className = 'toasts'; document.body.append(box); }
    const item = document.createElement(onClick ? 'button' : 'div');
    item.className = `toast ${kind}${onClick ? ' clickable' : ''}`;
    item.setAttribute('role', 'status');
    const strong = document.createElement('strong');
    strong.textContent = title;
    const span = document.createElement('span');
    span.textContent = text;
    item.append(strong, span);
    if (onClick) item.addEventListener('click', () => { item.remove(); onClick(); });
    box.append(item);
    setTimeout(() => item.classList.add('hide'), onClick ? 12000 : 7000);
    setTimeout(() => item.remove(), onClick ? 12600 : 7600);
  }
  const known = new Map();  // id → estado ya mostrado, para avisar sólo de los cambios
  let trayTimer = null;
  function openDialog(title, nodes) {
    const dialog = $('[data-jobs-dialog]');
    if (!dialog) return;
    $('[data-dialog-title]', dialog).textContent = title;
    $('[data-dialog-body]', dialog).replaceChildren(...nodes);
    if (!dialog.open) dialog.showModal();
  }
  async function showJob(id) {
    const job = await getJSON(`/api/job/${id}`);
    const state = document.createElement('p');
    const pill = document.createElement('span');
    pill.className = `pill ${job.state !== 'done' ? 'warn' : job.ok ? 'ok' : 'bad'}`;
    pill.textContent = job.state !== 'done' ? `Running · ${job.elapsed} s` : job.ok ? `Finished in ${job.elapsed} s` : 'Failed';
    state.append(pill);
    const output = document.createElement('pre');
    output.className = 'output';
    output.textContent = job.result ? job.result.output || '(no output)' : 'Still running… the result will appear here.';
    const reload = document.createElement('button');
    reload.type = 'button'; reload.className = 'mc-btn small'; reload.textContent = 'Reload page';
    reload.addEventListener('click', () => location.reload());
    openDialog(job.label, [state, output, reload]);
    if (job.state === 'done' && !job.seen) {
      await fetch(`/api/job/${id}/seen`, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf() } });
      refreshTray();
    }
  }
  function showHistory(jobs) {
    const list = document.createElement('ul');
    list.className = 'job-list';
    for (const job of jobs) {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `job-entry ${job.state !== 'done' ? 'running' : job.ok ? 'ok' : 'bad'}${job.seen ? '' : ' unseen'}`;
      button.textContent = `${job.label} — ${job.state !== 'done' ? `running ${job.elapsed} s` : job.ok ? 'finished' : 'failed'}`;
      button.addEventListener('click', () => showJob(job.id));
      item.append(button);
      list.append(item);
    }
    openDialog('Operations', jobs.length ? [list] : [Object.assign(document.createElement('p'), { textContent: 'No operations yet.' })]);
  }
  async function refreshTray() {
    const tray = $('[data-tray]');
    if (!tray) return;
    let jobs;
    try { jobs = (await getJSON('/api/jobs')).jobs || []; } catch { return; }
    const running = jobs.filter((j) => j.state === 'running');
    const unseen = jobs.filter((j) => j.state === 'done' && !j.seen);
    tray.hidden = !jobs.length;
    tray.classList.toggle('busy', running.length > 0);
    $('.tray-text', tray).textContent = running.length ? `${running[0].label}…` : 'Jobs';
    const badge = $('.badge-count', tray);
    badge.hidden = !unseen.length;
    badge.textContent = String(unseen.length);
    badge.classList.toggle('bad', unseen.some((j) => !j.ok));
    for (const job of jobs) {
      const previous = known.get(job.id);
      if (previous === 'running' && job.state === 'done') {
        toast(job.ok ? 'ok' : 'bad', job.ok ? `${job.label}: done!` : `${job.label}: failed`, 'Click to see the result.',
              () => showJob(job.id));
      }
      known.set(job.id, job.state);
    }
    tray.onclick = () => showHistory(jobs);
    clearTimeout(trayTimer);
    trayTimer = setTimeout(refreshTray, running.length ? 3000 : 20000);
  }
  async function submitAsync(form) {
    const body = new URLSearchParams(new FormData(form));
    const buttons = $$('button', form);
    buttons.forEach((b) => { b.disabled = true; });
    try {
      const response = await fetch(form.action, { method: 'POST', credentials: 'same-origin', body,
        headers: { 'X-Requested-With': 'fetch', 'Content-Type': 'application/x-www-form-urlencoded' } });
      const reply = await response.json().catch(() => ({}));
      if (response.status === 202 && reply.job) {
        known.set(reply.job, 'running');
        toast('warn', `${reply.label}: started`, 'Keep browsing — you will be notified when it finishes.', () => showJob(reply.job));
        refreshTray();
      } else {
        toast('bad', 'Could not start', reply.error || `HTTP ${response.status}`);
      }
    } catch {
      toast('bad', 'Could not start', 'The panel could not be reached.');
    } finally {
      buttons.forEach((b) => { b.disabled = false; });
    }
  }

  // --- Confirmaciones, copiar direcciones -------------------------------------------------
  document.addEventListener('submit', (event) => {
    const form = event.target;
    const message = form.dataset.confirm;
    if (message && !confirm(message)) { event.preventDefault(); return; }
    if (form.dataset.playerAction) {
      event.preventDefault();
      playerAction(form);
      return;
    }
    if (form.hasAttribute('data-async') && $('[data-tray]')) {
      event.preventDefault();
      submitAsync(form);
    }
  });
  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-dialog-close]')) event.target.closest('dialog').close();
  });
  document.addEventListener('click', async (event) => {
    const code = event.target.closest('code.copy');
    if (!code) return;
    try { await navigator.clipboard.writeText(code.dataset.copy); code.classList.add('copied'); setTimeout(() => code.classList.remove('copied'), 1500); } catch { /* sin portapapeles */ }
  });

  // --- Estado en vivo ---------------------------------------------------------------------
  const width = (pct) => `w-${Math.max(0, Math.min(100, Math.round((pct || 0) / 5) * 5))}`;
  function setBar(container, pct) {
    const fill = container && $('.fill', container);
    if (fill) fill.className = `fill ${width(pct)}`;
  }
  function applyPulse(data) {
    const chip = $('[data-live="chip"]');
    const players = /There are (\d+) of a max of (\d+) players online:?\s*(.*)/.exec(data.players || '');
    if (chip) {
      chip.classList.toggle('online', data.running && data.health === 'healthy');
      chip.classList.toggle('offline', !data.running);
      $('.chip-text', chip).textContent = !data.running ? 'Offline' : data.health !== 'healthy' ? 'Starting…'
        : `Online · ${players ? `${players[1]}/${players[2]}` : '?'}`;
    }
    const online = $('[data-live="online"]');
    if (online && players) online.textContent = players[1];
    const list = $('[data-live="players"]');
    if (list && players) {
      const names = players[3].split(',').map((n) => n.trim()).filter(Boolean);
      const icon = $('.icon', list);
      list.replaceChildren(...(names.length ? names : [null]).map((name) => {
        const li = document.createElement('li');
        if (name === null) { li.className = 'muted'; li.textContent = 'Nobody online right now'; return li; }
        if (icon) li.append(icon.cloneNode());
        li.append(name);
        return li;
      }));
    }
    if (data.memory && data.memory.limit) {
      const text = $('[data-live="memory-text"]');
      if (text) text.textContent = `${(data.memory.used / 1073741824).toFixed(2)} / ${(data.memory.limit / 1073741824).toFixed(1)} GiB`;
      setBar($('[data-live="memory-bar"]'), (data.memory.used * 100) / data.memory.limit);
    }
    if (data.temperature) {
      const text = $('[data-live="temp-text"]');
      if (text) text.textContent = `${data.temperature.toFixed(1)} °C`;
      setBar($('[data-live="temp-bar"]'), ((data.temperature - 30) * 100) / 55);
    }
  }
  const pulse = async () => { try { applyPulse(await getJSON('/api/pulse')); } catch { /* red caída */ } };

  // --- Consola ---------------------------------------------------------------------------
  const COMMANDS = ['list', 'say', 'tell', 'msg', 'time set day', 'time set night', 'weather clear', 'weather rain',
    'gamemode survival', 'gamemode creative', 'tp', 'give', 'kick', 'ban', 'pardon', 'op', 'deop', 'difficulty',
    'whitelist list', 'worldborder get', 'tps', 'mspt', 'seed', 'save-all', 'gamerule', 'effect give', 'xp add',
    'bluemap', 'chunky progress', 'geyser list', 'plugins', 'version'];
  function classify(line) {
    if (/\b(WARN|WARNING)\b/.test(line)) return 'l-warn';
    if (/\b(ERROR|SEVERE|Exception)\b/.test(line)) return 'l-error';
    if (/joined the game|left the game|logged in with entity id|lost connection/.test(line)) return 'l-join';
    if (/\]: <[^>]+> /.test(line)) return 'l-chat';
    if (/\[(Geyser-Spigot|floodgate)\]/.test(line)) return 'l-geyser';
    return '';
  }
  function renderLine(text, cls) {
    const row = document.createElement('div');
    const match = /^(\[\d\d:\d\d:\d\d[^\]]*\])(.*)$/.exec(text);
    if (match) {
      const time = document.createElement('span');
      time.className = 'l-time';
      time.textContent = match[1];
      row.append(time, match[2]);
    } else {
      row.textContent = text;
    }
    row.className = cls ?? classify(text);
    return row;
  }
  function initConsole(root) {
    const box = $('[data-console]', root);
    if (!box) return;
    const log = $('[data-console-log]', box);
    const status = $('[data-console-status]', box);
    const input = $('#cmd', box);
    const form = $('.terminal-input', box);
    const local = [];  // líneas propias (comandos y respuestas) que se mantienen al refrescar
    let lastRemote = log.textContent;
    let history = store.get('mclan-console-history', []);
    let cursor = history.length;
    const atBottom = () => log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    const paint = (remote) => {
      const keep = atBottom();
      log.replaceChildren(...remote.split('\n').filter(Boolean).map((l) => renderLine(l)), ...local.map(([t, c]) => renderLine(t, c)));
      if (keep) log.scrollTop = log.scrollHeight;
    };
    paint(lastRemote);
    log.scrollTop = log.scrollHeight;
    every(3000, async () => {
      try {
        const data = await getJSON('/api/console?lines=200');
        status.classList.remove('paused'); status.textContent = 'live';
        if (data.log !== undefined && data.log !== lastRemote) { lastRemote = data.log; paint(lastRemote); }
      } catch { status.classList.add('paused'); status.textContent = 'reconnecting'; }
    });
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const command = input.value.trim().replace(/^\//, '');
      if (!command) return;
      history = [...history.filter((h) => h !== command), command].slice(-50);
      store.set('mclan-console-history', history);
      cursor = history.length;
      input.value = '';
      local.push([`> ${command}`, 'l-cmd']);
      paint(lastRemote);
      log.scrollTop = log.scrollHeight;
      try {
        const response = await fetch('/api/console', { method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() }, body: JSON.stringify({ command }) });
        const data = await response.json();
        for (const line of String(data.output || data.error || '').split('\n')) local.push([`  ${line}`, data.ok ? 'l-out' : 'l-error']);
      } catch { local.push(['  (the panel could not reach the server)', 'l-error']); }
      while (local.length > 60) local.shift();
      paint(lastRemote);
      log.scrollTop = log.scrollHeight;
    });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowUp' && history.length) { event.preventDefault(); cursor = Math.max(0, cursor - 1); input.value = history[cursor]; }
      else if (event.key === 'ArrowDown') { event.preventDefault(); cursor = Math.min(history.length, cursor + 1); input.value = history[cursor] || ''; }
      else if (event.key === 'Tab') {
        event.preventDefault();
        const value = input.value.trimStart();
        const words = value.split(' ');
        // Nombres de la última respuesta de "list" (propia o en el log).
        const listed = [...local.map(([t]) => t), ...lastRemote.split('\n')].reverse().find((t) => /players online:/.test(t)) || '';
        const names = (/players online:?\s*(.*)$/.exec(listed)?.[1] || '').split(',').map((n) => n.trim()).filter(Boolean);
        const pool = words.length > 1 ? names : COMMANDS;
        const prefix = words.length > 1 ? words[words.length - 1] : value;
        const match = pool.find((c) => c.toLowerCase().startsWith(prefix.toLowerCase()) && c !== prefix);
        if (match) input.value = words.length > 1 ? [...words.slice(0, -1), match].join(' ') + ' ' : match + ' ';
      }
    });
    $$('[data-command]', box).forEach((chip) => chip.addEventListener('click', () => { input.value = chip.dataset.command; input.focus(); }));
    input.focus({ preventScroll: true });
  }

  // --- Subida de mundos: XHR para tener progreso; al terminar se lanza la importación ---------
  function initUpload(root) {
    const form = $('[data-upload]', root);
    if (!form) return;
    const input = $('input[type=file]', form);
    const name = $('input[name=name]', form);
    const bar = $('[data-upload-bar]', form);
    const status = $('[data-upload-status]', form);
    const button = $('button', form);
    input.addEventListener('change', () => {
      const file = input.files[0];
      if (file && !name.value) name.value = file.name.replace(/\.(zip|mcworld)$/i, '').slice(0, 40);
    });
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const file = input.files[0];
      if (!file) return;
      const safeName = file.name.replace(/[^A-Za-z0-9._ -]/g, '_').replace(/^[^A-Za-z0-9]+/, '');
      // Comprobación previa: el servidor rechaza sin leer el cuerpo y el navegador sólo vería un corte.
      try {
        const check = await getJSON(`/api/upload-check?name=${encodeURIComponent(safeName)}&size=${file.size}`);
        if (!check.ok) { status.textContent = `Cannot upload: ${check.error}`; return; }
      } catch { status.textContent = 'The panel could not be reached.'; return; }
      const request = new XMLHttpRequest();
      request.open('POST', '/upload');
      request.setRequestHeader('X-CSRF-Token', csrf());
      request.setRequestHeader('X-Filename', encodeURIComponent(safeName));
      request.setRequestHeader('Content-Type', 'application/octet-stream');
      bar.hidden = false;
      button.disabled = true;
      request.upload.addEventListener('progress', (e) => {
        if (!e.lengthComputable) return;
        const pct = (e.loaded * 100) / e.total;
        $('.fill', bar).className = `fill ${width(pct)}`;
        status.textContent = `Uploading… ${Math.round(pct)}% (${(e.loaded / 1048576).toFixed(0)} / ${(e.total / 1048576).toFixed(0)} MiB)`;
      });
      request.addEventListener('load', () => {
        let reply = {};
        try { reply = JSON.parse(request.responseText); } catch { /* respuesta no JSON */ }
        if (request.status === 200 && reply.ok) {
          status.textContent = 'Uploaded. The import runs in the background.';
          form.elements.file.value = reply.name;
          button.disabled = false;
          bar.hidden = true;
          submitAsync(form);  // crea la tarea de importación sin salir de la página
        } else {
          status.textContent = `Upload failed: ${reply.error || request.status}`;
          button.disabled = false;
        }
      });
      request.addEventListener('error', () => { status.textContent = 'Upload failed: the connection was interrupted.'; button.disabled = false; });
      request.send(file);
    });
  }

  // --- Acciones de jugador: API JSON, aviso y refresco parcial del perfil --------------------
  async function refreshProfile() {
    try {
      const response = await fetch(location.href, { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } });
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      for (const selector of ['.nameplate', '.r-vitals', '.r-location', '.r-inventory', '.pa-admin-panel']) {
        const fresh = $(selector, doc);
        const current = $(selector);
        if (fresh && current) current.replaceWith(fresh);
      }
    } catch { /* la próxima visita mostrará los datos nuevos */ }
  }
  async function playerAction(form) {
    const fields = Object.fromEntries(new FormData(form));
    delete fields.csrf;
    const buttons = $$('button', form);
    buttons.forEach((b) => { b.disabled = true; });
    try {
      const response = await fetch(form.dataset.playerAction, { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() }, body: JSON.stringify(fields) });
      if (response.status === 401) { location.href = '/login'; return; }
      const reply = await response.json().catch(() => ({}));
      if (reply.ok) {
        toast('ok', reply.message, reply.output || '');
        if (fields.do === 'message') form.reset();
        setTimeout(refreshProfile, 700);
      } else {
        toast('bad', 'Could not do that', reply.error || `HTTP ${response.status}`);
      }
    } catch {
      toast('bad', 'Could not do that', 'The panel could not be reached.');
    } finally {
      buttons.forEach((b) => { b.disabled = false; });
    }
  }
  let catalog = null;
  async function items() {
    if (!catalog) catalog = getJSON('/api/items').then((data) => data.items || []).catch(() => { catalog = null; return []; });
    return catalog;
  }
  function initPlayer(root) {
    const tabs = $('[data-tabs]', root);
    if (!tabs) return;
    const saved = store.get('pa-tab', '');
    const radio = saved && $(`#${CSS.escape(saved)}`, tabs);
    if (radio && !$('.alert.warn', tabs.parentElement)) radio.checked = true;
    tabs.addEventListener('change', (event) => { if (event.target.name === 'pa-tab') store.set('pa-tab', event.target.id); });

    const search = $('[data-item-search]', root);
    const results = $('[data-item-results]', root);
    const choose = (id) => {
      search.value = id;
      $$('.item-result', results).forEach((b) => b.classList.toggle('selected', b.dataset.item === id));
    };
    let timer = null;
    const render = async () => {
      const query = search.value.trim().toLowerCase();
      if (query.length < 2) { results.replaceChildren(); return; }
      const key = query.replace(/\s+/g, '_');
      const found = (await items()).filter(([id, name]) => id.includes(key) || name.toLowerCase().includes(query))
        .sort((a, b) => Number(!a[1].toLowerCase().startsWith(query)) - Number(!b[1].toLowerCase().startsWith(query))
          || a[1].length - b[1].length)
        .slice(0, 48);
      results.replaceChildren(...found.map(([id, name, icon]) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `item-result${id === search.value ? ' selected' : ''}`;
        button.dataset.item = id;
        button.title = id;
        if (icon) {
          const img = document.createElement('img');
          img.className = 'item-icon';
          img.src = `/assets/${icon}.png`;
          img.alt = '';
          img.loading = 'lazy';
          button.append(img);
        }
        const label = document.createElement('span');
        label.textContent = name;
        button.append(label);
        return button;
      }));
      if (!found.length) {
        const empty = document.createElement('p');
        empty.className = 'muted';
        empty.textContent = 'No item matches that name.';
        results.replaceChildren(empty);
      }
    };
    if (search && results) {
      search.addEventListener('focus', items, { once: true });
      search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(render, 120); });
      results.addEventListener('click', (event) => {
        const button = event.target.closest('.item-result');
        if (button) choose(button.dataset.item);
      });
      $$('.item-pick', root).forEach((b) => b.addEventListener('click', () => { search.value = b.dataset.item; render().then(() => choose(b.dataset.item)); }));
      const count = $('input[name="count"]', search.form);
      $$('[data-count]', root).forEach((b) => b.addEventListener('click', () => { count.value = b.dataset.count; }));
    }

    const enchant = $('[data-enchant]', root);
    const level = $('[data-enchant-level]', root);
    const limit = () => {
      const max = Number(enchant.selectedOptions[0]?.dataset.max || 5);
      $$('option', level).forEach((o) => { o.disabled = Number(o.value) > max; });
      if (Number(level.value) > max) level.value = String(max);
    };
    if (enchant && level) { enchant.addEventListener('change', limit); limit(); }

    const counter = $('[data-counter]', root);
    const out = $('[data-counter-out]', root);
    if (counter && out) counter.addEventListener('input', () => { out.textContent = String(counter.value.length); });
  }

  // --- Tareas largas ----------------------------------------------------------------------
  function initJob(root) {
    const marker = $('[data-job]', root);
    if (!marker) return;
    const elapsed = $('[data-job-elapsed]', root);
    every(2000, async () => {
      try {
        const data = await getJSON(`/api/job/${marker.dataset.job}`);
        if (elapsed) elapsed.textContent = data.elapsed;
        if (data.state === 'done') location.reload();
      } catch { /* se reintenta */ }
    });
  }

  function init(root) {
    $$('.toast', root).forEach((toast) => setTimeout(() => toast.classList.add('hide'), 7000));
    if ($('[data-live]')) every($('[data-live="online"]') ? 10000 : 20000, pulse);
    initConsole(root);
    initUpload(root);
    initJob(root);
    initPlayer(root);
    onScroll();
  }
  init(document);
  refreshTray();
})();
