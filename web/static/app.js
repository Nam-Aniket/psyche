/* Psyche web app — vanilla JS, wired to the FastAPI backend.
   Top tab nav + single-screen-at-a-time: landing · setup · upload · graph · chat. */
(() => {
  'use strict';

  // ── tiny DOM helpers ──────────────────────────────────────────────────────
  const SVGNS = 'http://www.w3.org/2000/svg';
  function h(tag, props, ...kids) { const e = document.createElement(tag); apply(e, props); add(e, kids); return e; }
  function s(tag, props) { const e = document.createElementNS(SVGNS, tag); if (props) for (const k in props) e.setAttribute(k, props[k]); return e; }
  function apply(e, props) {
    if (!props) return;
    for (const k in props) {
      const v = props[k];
      if (v == null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'html') e.innerHTML = v;
      else if (k === 'style') { typeof v === 'string' ? (e.style.cssText = v) : Object.assign(e.style, v); }
      else if (k.startsWith('on')) e.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === 'dataset') Object.assign(e.dataset, v);
      else e.setAttribute(k, v === true ? '' : v);
    }
  }
  function add(e, kids) { for (const k of kids.flat(Infinity)) { if (k == null || k === false) continue; e.appendChild(k instanceof Node ? k : document.createTextNode(String(k))); } }
  const clear = (e) => { while (e && e.firstChild) e.removeChild(e.firstChild); };

  function icon(path, size = 18, sw = 1.8) {
    const e = s('svg', { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': sw, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' });
    for (const d of (Array.isArray(path) ? path : [path])) e.appendChild(s('path', { d }));
    return e;
  }
  const ICONS = {
    gear: 'M12 2a3 3 0 0 0-3 3v1.2a6 6 0 0 0-2 1.16L5.7 7a3 3 0 1 0-3 5.2L4 13a6 6 0 0 0 0 2l-1.3.8a3 3 0 1 0 3 5.2l1.3-.77a6 6 0 0 0 2 1.16V22a3 3 0 0 0 6 0v-.6a6 6 0 0 0 2-1.16l1.3.77a3 3 0 1 0 3-5.2L20 15a6 6 0 0 0 0-2l1.3-.8a3 3 0 1 0-3-5.2L17 7.6a6 6 0 0 0-2-1.16V5a3 3 0 0 0-3-3z',
    sun: 'M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4',
    arrow: 'M5 12h14M13 6l6 6-6 6',
    upload: ['M12 16V4M7 9l5-5 5 5', 'M5 16v2a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-2'],
    chat: 'M21 11.5a8.4 8.4 0 0 1-9 8.4 9 9 0 0 1-3.6-.6L3 21l1.3-4a8.3 8.3 0 0 1-.8-3.6A8.4 8.4 0 1 1 21 11.5z',
    list: ['M4 7h16M4 12h16M4 17h10'],
    close: ['M5 5l14 14M19 5L5 19'],
    refresh: ['M21 12a9 9 0 1 1-2.64-6.36M21 4v5h-5'],
    trash: ['M4 7l1.5 12.5A1.5 1.5 0 0 0 7 21h10a1.5 1.5 0 0 0 1.5-1.5L20 7', 'M2 7h20M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2'],
  };
  function navIconFor(key) {
    const m = { setup: ICONS.gear, upload: ICONS.upload, graph: 'M6 6a2 2 0 1 0 0 .01M18 9a2 2 0 1 0 0 .01M9 18a2 2 0 1 0 0 .01M8 7l8 1.4M7.6 16.2l1.8-7.6', chat: ICONS.chat };
    return icon(m[key], 16);
  }
  function themeIcon() { const e = icon(ICONS.sun, 17); e.insertBefore(s('circle', { cx: 12, cy: 12, r: 4 }), e.firstChild); return e; }
  function circlesGraphIcon(size = 23) {
    const e = s('svg', { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.8, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' });
    e.append(s('circle', { cx: 6, cy: 6, r: 2.4 }), s('circle', { cx: 18, cy: 9, r: 2.4 }), s('circle', { cx: 9, cy: 18, r: 2.4 }), s('path', { d: 'M8 7.2l7.6 1.4M7.6 16.2l1.8-7.6' }));
    return e;
  }
  // ── API client ────────────────────────────────────────────────────────────
  const api = {
    async req(method, path, body, isForm) {
      const opts = { method, headers: {} };
      if (body !== undefined) { if (isForm) opts.body = body; else { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); } }
      const res = await fetch(path, opts);
      let data = null; try { data = await res.json(); } catch (_) {}
      if (!res.ok) { const err = new Error((data && data.detail) || res.statusText || 'Request failed'); err.status = res.status; err.detail = data && data.detail; throw err; }
      return data;
    },
    get(p) { return this.req('GET', p); },
    post(p, b) { return this.req('POST', p, b); },
    del(p) { return this.req('DELETE', p); },
    upload(form) { return this.req('POST', '/ingest/upload', form, true); },
  };

  // ── state ───────────────────────────────────────────────────────────────
  const state = {
    screen: 'landing',
    theme: localStorage.getItem('psyche-theme') || 'light',
    provider: null, sources: [], nodes: [], edges: [],
    currentScreen: null, connected: {}, llmPath: 'agent',
    chat: [], thinking: false, ingesting: [], loadedApp: false,
  };
  const SCREENS = ['setup', 'upload', 'graph', 'chat'];
  const SCREEN_META = {
    setup: { tab: 'Setup', kicker: 'Setup', title: 'Connect your agents' },
    upload: { tab: 'Upload', kicker: 'Sources', title: 'Your library' },
    graph: { tab: 'Graph', kicker: 'Knowledge graph', title: 'Concepts & connections' },
    chat: { tab: 'Chat', kicker: 'Ask', title: 'Ask your library' },
  };
  let currentScreen = null;

  const root = document.getElementById('root');
  const dark = () => state.theme === 'dark';
  const reduceMotion = () => !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

  function setTheme(t) {
    state.theme = t;
    document.documentElement.setAttribute('data-theme', t);
    localStorage.setItem('psyche-theme', t);
    document.querySelectorAll('[data-theme-label]').forEach((el) => (el.textContent = t === 'dark' ? 'Light' : 'Dark'));
    if (graph) graph.refreshTheme();
  }
  function toast(msg, isError) {
    const t = h('div', { class: 'toast' + (isError ? ' is-error' : ''), role: 'status', 'aria-live': 'polite' }, msg);
    document.body.appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity .3s'; setTimeout(() => t.remove(), 320); }, 2600);
  }

  // ── colors (by hue) ────────────────────────────────────────────────────────
  const KNOWN_HUE = { caching: 300, retrieval: 263, memory: 332, models: 288, learning: 354 };
  const FALLBACK_HUES = [300, 263, 332, 210, 150, 35, 95, 180, 50, 320];
  const cssId = (x) => String(x).replace(/[^a-z0-9]/gi, '_');
  const catLabel = (c) => (c ? String(c).charAt(0).toUpperCase() + String(c).slice(1) : 'General');
  function strokeForHue(H) { return dark() ? `oklch(0.74 0.13 ${H})` : `oklch(0.55 0.16 ${H})`; }
  function baseForHue(H) { return dark() ? `oklch(0.66 0.15 ${H})` : `oklch(0.6 0.16 ${H})`; }
  function gradStopsForHue(H, id) { const base = baseForHue(H), d = dark(); return { id, hi: `color-mix(in oklab, ${base}, white ${d ? 36 : 52}%)`, mid: base, lo: `color-mix(in oklab, ${base}, black ${d ? 30 : 24}%)` }; }

  // ── data loading ──────────────────────────────────────────────────────────
  async function loadAppData() {
    const [provider, sources, nodes, edges] = await Promise.all([
      api.get('/provider').catch(() => null),
      api.get('/sources').catch(() => []),
      api.get('/graph/nodes').catch(() => []),
      api.get('/graph/edges').catch(() => []),
    ]);
    state.provider = provider; state.sources = sources || []; state.nodes = nodes || []; state.edges = edges || [];
    if (provider) state.llmPath = provider.chat_model && provider.chat_model !== 'none'
      ? (provider.chat_provider === 'ollama' || provider.provider === 'ollama' ? 'ollama' : 'apikey') : 'agent';
  }
  const chunkTotal = () => state.sources.reduce((a, b) => a + (b.chunk_count || 0), 0);

  // ── router ────────────────────────────────────────────────────────────────
  function go(screen, section) { if (screen === 'landing') { state.screen = 'landing'; render(); return; } enterApp(section || 'graph'); }
  async function enterApp(target) {
    if (state.screen !== 'app') {
      state.screen = 'app'; currentScreen = null; render();
      if (!state.loadedApp) { try { await loadAppData(); state.loadedApp = true; } catch (e) { console.error(e); } }
      showScreen(target, false);
    } else { showScreen(target, true); }
  }

  // ── top-level render ────────────────────────────────────────────────────────
  let graph = null;
  function render() {
    document.documentElement.setAttribute('data-theme', state.theme);
    if (graph) { graph.dispose(); graph = null; }
    if (window.PsycheLanding) window.PsycheLanding.destroy();
    root.replaceChildren(state.screen === 'landing' ? Landing() : App());
  }

  // ── Landing (scroll-driven cinematic) ───────────────────────────────────────
  const CAPS = [
    { step: '01 · Ingest', h: 'Drop in your library.', p: 'PDFs, EPUBs, Markdown, notes, chunked and embedded on your machine. Nothing leaves.' },
    { step: '02 · Embed', h: 'Every idea, a coordinate.', p: 'Local ONNX embeddings turn your words into vectors, searchable by meaning, not just keywords.' },
    { step: '03 · Link', h: 'Ideas find each other.', p: 'Related concepts cluster and connect, forming a living map of how your knowledge fits together.' },
    { step: '04 · Recall', h: 'A mind you can question.', p: 'Ask in plain language, get cited answers, and one shared memory across every AI agent you use.' },
  ];
  function Landing() {
    const wrap = h('div', { class: 'app-root landing' });
    const hero = h('section', { class: 'lh-hero' },
      h('canvas', { class: 'lh-hero-canvas', id: 'lh-hero-canvas', 'aria-hidden': 'true' }),
      h('main', { class: 'landing__main' },
        h('div', { class: 'hero' },
          h('div', { class: 'eyebrow' }, h('span', { class: 'eyebrow__dot' }), 'Runs entirely on your machine'),
          (() => { const hh = h('h1'); hh.innerHTML = 'A private second brain<br>your <em>whole toolchain</em> can remember.'; return hh; })(),
          h('p', {}, 'Psyche turns your docs, books and notes into a private, searchable mind, and shares one cited memory across every AI coding agent you use.'),
          h('div', { class: 'cta-row' },
            h('button', { class: 'btn btn--primary', onClick: () => go('app', 'graph') }, 'Open the app', icon(ICONS.arrow, 18, 2.2)),
            h('button', { class: 'btn btn--soft', onClick: () => go('app', 'setup') }, icon(ICONS.gear, 18, 1.9), 'Connect your agent'),
          ),
          h('div', { class: 'feat-list' },
            h('span', {}, h('b', {}, '●'), ' Nothing leaves your machine'),
            h('span', {}, h('b', {}, '●'), ' PDF · EPUB · Markdown · DOCX'),
            h('span', {}, h('b', {}, '●'), ' Works offline with Ollama'),
          ),
        ),
      ),
      h('button', { class: 'scroll-cue', id: 'scroll-cue', type: 'button', 'aria-label': 'Scroll to see how Psyche works' },
        h('span', { class: 'scroll-cue__label' }, 'See how it works'),
        h('span', { class: 'scroll-cue__mouse' }, h('span', { class: 'scroll-cue__dot' })),
        h('span', { class: 'scroll-cue__chev', 'aria-hidden': 'true' }, icon('M6 9l6 6 6-6', 18, 2.4)),
      ),
    );

    const caps = h('div', { class: 'lh-captions' });
    CAPS.forEach((c, i) => caps.appendChild(h('div', { class: 'lh-cap', id: 'lh-cap-' + i },
      h('span', { class: 'lh-step' }, c.step), h('h2', {}, c.h), h('p', {}, c.p))));
    const scrolly = h('section', { class: 'lh-scrolly', id: 'lh-scrolly' },
      h('div', { class: 'lh-stage', id: 'lh-stage' },
        h('canvas', { class: 'lh-canvas', id: 'lh-canvas', 'aria-hidden': 'true' }),
        caps));

    const agents = h('section', { class: 'lh-section lh-agents reveal' },
      h('h2', {}, 'One memory. Every agent.'),
      h('p', {}, 'A preference stated in Codex is known to Claude Code. A lesson learned in Antigravity follows you everywhere: a shared, local fact store, billed $0.'),
      h('div', { class: 'lh-agent-row' },
        h('div', { class: 'lh-agent' }, 'Claude Code'), h('div', { class: 'lh-agent' }, 'Codex'),
        h('div', { class: 'lh-core', title: 'Psyche' }),
        h('div', { class: 'lh-agent' }, 'Gemini'), h('div', { class: 'lh-agent' }, 'Antigravity')));

    const cta = h('section', { class: 'lh-section lh-cta reveal' },
      (() => { const hh = h('h2'); hh.innerHTML = 'Give your agents a <em>memory</em>.'; return hh; })(),
      h('div', { class: 'cta-row', style: 'justify-content:center' },
        h('button', { class: 'btn btn--primary', onClick: () => go('app', 'graph') }, 'Open the app', icon(ICONS.arrow, 18, 2.2)),
        h('button', { class: 'btn btn--soft', onClick: () => go('app', 'upload') }, icon(ICONS.upload, 18, 1.9), 'Add documents')));

    wrap.append(
      h('div', { class: 'lh-progress', id: 'lh-progress', 'aria-hidden': 'true' }),
      h('header', { class: 'landing__header' },
        brand(false), h('span', { class: 'tag-pill', style: 'margin-left:6px' }, 'v0.7 · local'),
        h('nav', { class: 'landing__nav' }, themeBtn(), h('button', { class: 'btn btn--soft', onClick: () => go('app', 'graph') }, 'Open the app'))),
      hero, scrolly, agents, cta,
      h('div', { class: 'lh-foot' }, 'Psyche · local-first knowledge & memory for AI agents'),
    );
    const initLanding = () => { if (state.screen === 'landing' && window.PsycheLanding) window.PsycheLanding.init(); };
    requestAnimationFrame(() => requestAnimationFrame(initLanding));
    setTimeout(initLanding, 140); // fallback if rAF is throttled (init is idempotent)
    return wrap;
  }
  function brand(small) {
    return h('button', { class: 'brand', onClick: () => go('landing') }, h('div', { class: 'brand__mark' }), h('span', { class: 'brand__name', style: small ? 'font-size:20px' : '' }, 'Psyche'));
  }
  function themeBtn() { const b = h('button', { class: 'btn btn--icon', title: 'Toggle theme', 'aria-label': 'Toggle light or dark theme', onClick: () => setTheme(dark() ? 'light' : 'dark') }); b.appendChild(themeIcon()); return b; }

  // ── App shell: top tab bar + single screen ──────────────────────────────────
  function App() {
    const wrap = h('div', { class: 'app' });
    const tabs = h('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Sections' }, h('div', { class: 'tab-pill', id: 'tab-pill' }));
    SCREENS.forEach((key) => tabs.appendChild(h('button', { class: 'tab', id: 'tab-' + key, role: 'tab', 'aria-selected': 'false', onClick: () => showScreen(key, true) }, navIconFor(key), SCREEN_META[key].tab)));
    const appbar = h('div', { class: 'appbar' },
      h('div', { class: 'appbar__brand' }, brand(true)),
      tabs,
      h('div', { class: 'appbar__right' },
        h('div', { class: 'stat-chip', id: 'stat-chip' }),
        themeBtn(),
      ),
    );
    const host = h('div', { class: 'screen-host', id: 'screen-host' });
    wrap.append(appbar, host);
    return wrap;
  }
  function updateStatChip() {
    const c = document.getElementById('stat-chip'); if (!c) return;
    clear(c);
    c.append(h('span', { class: 'stat-chip__dot' }), `${state.sources.length} sources · ${chunkTotal().toLocaleString()} chunks · ${state.nodes.length} concepts`);
  }
  function movePill(key) {
    const t = document.getElementById('tab-' + key), pill = document.getElementById('tab-pill');
    if (t && pill) { pill.style.width = t.offsetWidth + 'px'; pill.style.transform = `translateX(${t.offsetLeft}px)`; }
  }
  function buildScreen(key) {
    if (key === 'setup') return buildSetup();
    if (key === 'upload') return buildUpload();
    if (key === 'graph') return buildGraph();
    return buildChat();
  }
  function showScreen(key, animate) {
    const host = document.getElementById('screen-host'); if (!host) return;
    const dir = SCREENS.indexOf(key) >= SCREENS.indexOf(currentScreen || 'setup') ? 'down' : 'up';
    if (currentScreen === 'graph' && graph) { graph.dispose(); graph = null; }
    currentScreen = key;
    SCREENS.forEach((k) => { const t = document.getElementById('tab-' + k); if (t) { const on = k === key; t.classList.toggle('is-active', on); t.setAttribute('aria-selected', on ? 'true' : 'false'); } });
    const node = buildScreen(key);
    if (animate !== false) node.classList.add(dir === 'down' ? 'ps-dir-down' : 'ps-dir-up');
    host.replaceChildren(node);
    movePill(key);
    updateStatChip();
  }
  function refreshCurrent() {
    if (state.screen !== 'app' || !currentScreen) return;
    const host = document.getElementById('screen-host'); if (!host) return;
    if (currentScreen === 'graph' && graph) { graph.dispose(); graph = null; }
    host.replaceChildren(buildScreen(currentScreen));
    updateStatChip();
  }
  function screenHead(key, extra) {
    const m = SCREEN_META[key];
    return h('div', { class: 'screen-head' }, h('div', { class: 'section__kicker' }, m.kicker), h('h1', {}, m.title), extra || null);
  }

  // ── Setup ─────────────────────────────────────────────────────────────────
  const PATHS = [
    { id: 'apikey', name: 'Gemini API key', tag: 'cloud chat', desc: 'Use a free Google Gemini key for in-app chat & fact extraction. Embeddings still run locally.', field: { label: 'Gemini API key', type: 'password', placeholder: 'AIza…  (free at aistudio.google.com)' }, chat_provider: 'gemini' },
    { id: 'ollama', name: 'Local Ollama', tag: 'fully offline', desc: 'A local chat model plus local embeddings. $0, no network, nothing leaves your disk.', field: { label: 'model', type: 'text', placeholder: 'llama3' }, chat_provider: 'ollama' },
    { id: 'agent', name: 'Agent-only', tag: 'no chat model', desc: 'Skip the in-app chat model. Facts accumulate via your coding agent over MCP; search runs on local ONNX.', chat_provider: 'none' },
  ];
  const CLIENT_META = {
    'claude-code': { name: 'Claude Code', glyph: 'CC', via: 'lifecycle hooks · zero model overhead', target: '~/.claude/settings.json' },
    codex: { name: 'Codex', glyph: 'CX', via: 'MCP + AGENTS.md protocol', target: '~/.codex/AGENTS.md' },
    gemini: { name: 'Gemini', glyph: 'GM', via: 'MCP + global rules', target: '~/.gemini/GEMINI.md' },
    antigravity: { name: 'Antigravity', glyph: 'AG', via: 'MCP + global rules', target: '~/.gemini/GEMINI.md' },
  };
  let supportedClients = ['claude-code', 'codex', 'gemini', 'antigravity'];
  function buildSetup() {
    const screen = h('div', { class: 'screen' });
    const pad = h('div', { class: 'screen-pad' }, h('div', { class: 'section-wrap', id: 'setup-wrap' }));
    screen.appendChild(pad);
    drawSetup(pad.firstChild);
    api.get('/supported-clients').then((c) => { if (Array.isArray(c) && c.length) { supportedClients = c; drawSetup(); } }).catch(() => {});
    return screen;
  }
  function drawSetup(wrapArg) {
    const wrap = wrapArg || document.getElementById('setup-wrap'); if (!wrap) return;
    clear(wrap);
    const left = h('div', {},
      h('div', { class: 'step-label' }, h('span', { class: 'step-num' }, '1'), 'Choose an LLM path'),
      h('div', { class: 'path-list' }, PATHS.map((p) => pathOption(p))),
      h('div', { class: 'path-note' }, 'Your active path is set in ', h('code', {}, '.env'), '. Selecting another here previews what it wires.'),
      h('div', { class: 'step-label' }, h('span', { class: 'step-num' }, '2'), 'Connect your agent'),
      h('div', { class: 'agent-grid' }, supportedClients.map((c) => agentCard(c))),
    );
    wrap.append(
      screenHead('setup', h('p', { class: 'section__sub' }, 'Embeddings always run locally. Pick how chat & extraction happen, then wire Psyche into your tools.')),
      h('div', { class: 'setup-grid' }, left, wiredPanel()),
    );
  }
  function activePathId() {
    const p = state.provider; if (!p) return 'agent';
    return (p.chat_model && p.chat_model !== 'none')
      ? ((p.chat_provider === 'ollama' || p.provider === 'ollama') ? 'ollama' : 'apikey') : 'agent';
  }
  function pathOption(p) {
    const on = state.llmPath === p.id;
    const active = p.id === activePathId();
    let input = null;
    if (on && p.field) input = h('input', { type: p.field.type, placeholder: p.field.placeholder });
    const saveBtn = on
      ? h('button', { class: 'btn btn--primary', style: 'margin-top:14px;font-size:13.5px;padding:9px 16px',
          onClick: (e) => { e.stopPropagation(); saveChatProvider(p, input ? input.value.trim() : ''); } },
          active ? 'Re-save' : 'Save chat setting')
      : null;
    return h('div', { class: 'path-option' + (on ? ' is-selected' : ''), role: 'radio', 'aria-checked': on ? 'true' : 'false', tabindex: '0',
      onClick: () => { state.llmPath = p.id; drawSetup(); },
      onKeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); state.llmPath = p.id; drawSetup(); } } },
      h('span', { class: 'path-radio' }),
      h('span', { style: 'flex:1' },
        h('span', { style: 'display:flex;align-items:center;gap:10px' }, h('span', { class: 'path-option__name' }, p.name), h('span', { class: 'path-option__tag' }, p.tag), active ? h('span', { class: 'path-active' }, 'Active') : null),
        h('span', { class: 'path-option__desc' }, p.desc),
        input ? h('label', { class: 'field', onClick: (e) => e.stopPropagation() }, h('span', { class: 'field__label' }, p.field.label), input) : null,
        saveBtn,
      ),
    );
  }
  async function saveChatProvider(p, value) {
    const payload = { chat_provider: p.chat_provider };
    if (p.id === 'apikey') { if (!value) { toast('Enter your Gemini API key first', true); return; } payload.api_key = value; }
    if (p.id === 'ollama' && value) payload.chat_model = value;
    try {
      const res = await api.post('/provider', payload);
      state.provider = res;
      toast(p.chat_provider === 'none' ? 'Chat disabled — search stays local' : `Chat enabled · ${res.chat_provider} · ${res.chat_model}`);
      drawSetup();
    } catch (e) {
      toast(e.detail || e.message || 'Could not save', true);
    }
  }
  function agentCard(key) {
    const m = CLIENT_META[key] || { name: key, glyph: key.slice(0, 2).toUpperCase(), via: 'MCP server', target: '' };
    const on = !!state.connected[key];
    return h('div', { class: 'agent-card' + (on ? ' is-connected' : '') },
      h('div', { class: 'agent-card__top' }, h('span', { class: 'agent-card__glyph' }, m.glyph), h('span', { class: 'agent-card__name' }, m.name)),
      h('div', { class: 'agent-card__via' }, m.via),
      h('button', { class: 'btn ' + (on ? 'btn--primary' : 'btn--soft'), onClick: () => connectAgent(key) }, on ? 'Connected ✓' : 'Connect'),
    );
  }
  async function connectAgent(key) {
    if (state.connected[key]) { state.connected[key] = false; drawSetup(); return; }
    try { const r = await api.post('/connect', { client: key, dry_run: false }); state.connected[key] = (r && r.actions) || []; drawSetup(); toast(`${(CLIENT_META[key] || {}).name || key} connected · ${(r.actions || []).length} actions`); }
    catch (e) { toast(e.detail || e.message || 'Connect failed', true); }
  }
  function wiredPanel() {
    const p = state.provider || {};
    const chatLine = state.llmPath === 'apikey' ? (p.chat_model && p.chat_model !== 'none' ? `${p.chat_provider} · ${p.chat_model}` : 'cloud API (set in .env)')
      : state.llmPath === 'ollama' ? 'local Ollama · offline' : 'none · MCP add_memory only';
    const connectedKeys = Object.keys(state.connected).filter((k) => state.connected[k]);
    const body = h('div', { class: 'wired-panel__body' },
      h('div', { class: 'k' }, '› embeddings'), h('div', { class: 'v' }, `local ONNX · ${p.embed_model || 'bge-small'} · offline`),
      h('div', { class: 'k' }, '› chat & extraction'), h('div', { class: 'v--accent' }, chatLine),
      h('div', { class: 'k' }, '› mcp server'), h('div', { class: 'v' }, h('span', {}, 'psyche start-mcp '), h('span', { class: 'k' }, '(stdio)')),
      h('div', { class: 'wired-panel__rule' }), h('div', { class: 'k', style: 'margin-bottom:8px' }, '› agents'),
    );
    if (connectedKeys.length) connectedKeys.forEach((k) => { const m = CLIENT_META[k] || { name: k, target: '' }; body.appendChild(h('div', { class: 'wired-agent' }, h('span', { class: 'wired-agent__tick' }, '✓'), h('span', { style: 'flex:1' }, h('span', { class: 'wired-agent__name' }, m.name), h('br'), h('span', { class: 'wired-agent__target' }, m.target)))); });
    else body.appendChild(h('div', { class: 'wired-empty' }, 'none connected yet · pick one above'));
    return h('div', { class: 'wired-panel' }, h('div', { class: 'wired-panel__bar' }, h('span', { class: 'wired-panel__dot' }), h('span', { class: 'wired-panel__dot' }), h('span', { class: 'wired-panel__dot' }), h('span', { class: 'wired-panel__title' }, 'what gets wired')), body);
  }

  // ── Upload ────────────────────────────────────────────────────────────────
  let confirmRemove = null, confirmClear = false, fileInput = null;
  const KNOWN_EXT = ['pdf', 'epub', 'md', 'markdown', 'docx', 'doc', 'txt', 'org', 'html', 'rtf'];
  function kindOf(title) {
    const dot = title.lastIndexOf('.');
    if (dot >= 0) { const ext = title.slice(dot + 1).toLowerCase(); if (KNOWN_EXT.includes(ext)) return (ext === 'markdown' ? 'MD' : ext).toUpperCase().slice(0, 4); }
    return 'DOC';
  }
  function buildUpload() {
    const screen = h('div', { class: 'screen' });
    const hasSources = state.sources.length > 0;
    const head = screenHead('upload', h('p', { class: 'section__sub' }, 'Everything is chunked, embedded and indexed on your machine. Nothing is uploaded.'));
    const headRow = h('div', { class: 'upload-head' }, head,
      hasSources ? (confirmClear
        ? h('span', { class: 'confirm-row' }, h('span', { class: 'confirm-row__q' }, 'Remove every source?'),
            h('button', { class: 'btn btn--soft', style: 'font-size:13px;padding:8px 13px', onClick: () => { confirmClear = false; refreshCurrent(); } }, 'Cancel'),
            h('button', { class: 'btn btn--danger', onClick: doClear }, 'Clear all'))
        : h('button', { class: 'btn btn--soft', style: 'font-size:13.5px;padding:9px 15px;color:var(--muted)', onClick: () => { confirmClear = true; refreshCurrent(); } }, 'Clear all')) : null);

    const dz = h('div', { class: 'dropzone', role: 'button', tabindex: '0', 'aria-label': 'Add documents: drag files here or activate to browse',
      onClick: openBrowse,
      onKeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openBrowse(e); } },
      onDragover: (e) => { e.preventDefault(); dz.classList.add('is-drag'); },
      onDragleave: (e) => { e.preventDefault(); dz.classList.remove('is-drag'); },
      onDrop: (e) => { e.preventDefault(); dz.classList.remove('is-drag'); handleFiles(e.dataTransfer && e.dataTransfer.files); } },
      h('div', { class: 'dropzone__icon' }, icon(ICONS.upload, 26)),
      h('div', { class: 'dropzone__title' }, 'Add documents to your brain'),
      h('div', { class: 'dropzone__sub' }, 'Drag files here, or click to browse'),
      h('div', { class: 'dropzone__exts' }, 'PDF · EPUB · Markdown · DOCX · Org · TXT'),
    );
    const ingestList = h('div', { class: 'ingest-list' });
    state.ingesting.forEach((f) => ingestList.appendChild(ingestRow(f)));

    const firstRun = state.provider && state.provider.provider === 'local' && !state.sources.length && !state.ingesting.length;
    const firstRunNote = firstRun
      ? h('div', { class: 'note-banner' }, h('span', { class: 'note-banner__dot' }),
          'Your first ingest downloads the local embedding model (~90 MB), one time. After that, everything runs fully offline.')
      : null;

    const wrap = h('div', { class: 'upload-wrap section-wrap' }, headRow, dz, firstRunNote, ingestList);
    if (hasSources) {
      const table = h('div', { class: 'sources-table' });
      state.sources.forEach((src) => table.appendChild(sourceRow(src)));
      const filter = state.sources.length > 8
        ? h('input', { class: 'sources-filter', type: 'search', placeholder: 'Filter sources…', 'aria-label': 'Filter sources',
            onInput: (e) => { const q = e.target.value.toLowerCase(); table.querySelectorAll('.source-row').forEach((r) => { r.style.display = (r.dataset.search || '').includes(q) ? '' : 'none'; }); } })
        : null;
      wrap.appendChild(h('div', { class: 'sources-head' },
        h('h2', {}, 'Ingested sources ', h('span', { class: 'count-pill' }, `${state.sources.length} · ${chunkTotal().toLocaleString()} chunks`)),
        filter));
      wrap.appendChild(table);
    } else if (state.ingesting.length === 0) {
      wrap.appendChild(h('div', { class: 'empty-state' },
        h('div', { class: 'empty-state__icon' }, icon(ICONS.trash, 28, 1.6)),
        h('h3', {}, 'No sources yet'),
        h('p', {}, 'Your knowledge base is empty. Add a few documents and Psyche will build your concept graph automatically.'),
        h('button', { class: 'btn btn--primary', onClick: openBrowse }, 'Add documents')));
    }
    screen.appendChild(h('div', { class: 'screen-pad' }, wrap));
    return screen;
  }
  function sourceRow(src) {
    const confirming = confirmRemove === src.id;
    return h('div', { class: 'source-row', dataset: { search: (src.title + ' ' + (src.author || '')).toLowerCase() } },
      h('span', { class: 'source-row__kind' }, kindOf(src.title)),
      h('span', { class: 'source-row__body' }, h('span', { class: 'source-row__title' }, src.title), h('span', { class: 'source-row__meta' }, (src.author || 'Unknown') + ' · local')),
      h('span', { class: 'source-row__chunks' }, `${src.chunk_count} chunks`),
      h('span', { class: 'source-row__status' }, 'indexed'),
      confirming
        ? h('span', { class: 'confirm-row' },
            h('button', { class: 'btn btn--soft', style: 'font-size:12.5px;padding:6px 11px', onClick: () => { confirmRemove = null; refreshCurrent(); } }, 'Cancel'),
            h('button', { class: 'btn btn--danger', style: 'font-size:12.5px;padding:6px 12px', onClick: () => doRemove(src.id) }, 'Remove'))
        : h('button', { class: 'btn btn--icon', style: 'width:28px;height:28px', title: 'Remove', 'aria-label': 'Remove ' + src.title, onClick: () => { confirmRemove = src.id; refreshCurrent(); } }, icon(ICONS.close, 14, 2)),
    );
  }
  function ingestRow(f) {
    return h('div', { class: 'ingest-row' },
      h('div', { class: 'ingest-row__top' }, h('span', { class: 'ingest-row__ext' }, kindOf(f.name)), h('span', { class: 'ingest-row__name' }, f.name), h('span', { class: 'ingest-row__status' + (f.error ? ' is-error' : '') }, f.error ? 'failed' : 'embedding…')),
      h('div', { class: 'progress' + (f.error ? '' : ' is-indeterminate') }, h('div', { style: f.error ? 'width:100%;background:var(--danger)' : '' })),
    );
  }
  function openBrowse(e) {
    if (e && e.stopPropagation) e.stopPropagation();
    if (!fileInput) { fileInput = h('input', { type: 'file', multiple: true, accept: '.pdf,.epub,.md,.markdown,.docx,.txt,.org,.html', style: 'display:none', onChange: () => { handleFiles(fileInput.files); fileInput.value = ''; } }); document.body.appendChild(fileInput); }
    fileInput.click();
  }
  async function handleFiles(files) {
    if (!files || !files.length) return;
    for (const file of Array.from(files)) {
      const entry = { id: 'ing-' + Date.now() + '-' + Math.round(performance.now()), name: file.name, error: false };
      state.ingesting.push(entry); refreshCurrent();
      try {
        const form = new FormData(); form.append('file', file); await api.upload(form);
        state.ingesting = state.ingesting.filter((x) => x.id !== entry.id);
        await refreshData(); refreshCurrent(); toast(`Ingested ${file.name}`);
      } catch (err) {
        entry.error = true; refreshCurrent(); toast(`${file.name}: ${err.detail || err.message}`, true);
        setTimeout(() => { state.ingesting = state.ingesting.filter((x) => x.id !== entry.id); refreshCurrent(); }, 4000);
      }
    }
  }
  async function refreshData() {
    const [sources, nodes, edges] = await Promise.all([api.get('/sources').catch(() => state.sources), api.get('/graph/nodes').catch(() => state.nodes), api.get('/graph/edges').catch(() => state.edges)]);
    state.sources = sources; state.nodes = nodes; state.edges = edges; updateStatChip();
  }
  async function doRemove(id) { confirmRemove = null; try { await api.del('/sources/' + id); await refreshData(); refreshCurrent(); toast('Source removed'); } catch (e) { toast(e.detail || e.message, true); refreshCurrent(); } }
  async function doClear() { confirmClear = false; try { const r = await api.del('/sources'); await refreshData(); refreshCurrent(); toast(`Removed ${r.deleted} sources`); } catch (e) { toast(e.detail || e.message, true); refreshCurrent(); } }

  // ── Graph ─────────────────────────────────────────────────────────────────
  function buildGraph() {
    const screen = h('div', { class: 'screen screen--flush' });
    if (graph) { graph.dispose(); graph = null; }
    const head = h('div', { class: 'graph-head' }, h('div', {}, h('div', { class: 'section__kicker' }, 'Knowledge graph'), h('h1', {}, 'Concepts & connections')));
    if (!state.nodes.length) {
      screen.appendChild(head);
      const hasSources = state.sources.length > 0;
      screen.appendChild(h('div', { class: 'graph-body', style: 'align-items:center;justify-content:center' },
        h('div', { class: 'empty-state', style: 'max-width:440px;border:0;background:transparent' },
          h('div', { class: 'empty-state__icon' }, circlesGraphIcon(28)),
          h('h3', {}, hasSources ? 'No concept graph yet' : 'Nothing to map yet'),
          h('p', {}, hasSources ? 'You have sources indexed but no concept graph. Build one by clustering your embeddings into concepts and relationships.' : 'Ingest a few documents first, then Psyche can extract concepts and map how they relate.'),
          hasSources ? h('button', { class: 'btn btn--primary', id: 'build-graph-btn', onClick: buildGraphAction }, 'Build the graph') : h('button', { class: 'btn btn--primary', onClick: () => showScreen('upload', true) }, 'Go to upload'))));
      return screen;
    }
    const controls = h('div', { class: 'graph-controls' });
    controls.appendChild(h('button', { class: 'btn btn--soft', style: 'font-size:13px;padding:8px 13px', title: 'Re-extract concepts from your sources', 'aria-label': 'Rebuild the concept graph', onClick: (e) => rebuildGraph(e.currentTarget) }, icon(ICONS.refresh, 15, 2), 'Rebuild'));
    head.appendChild(controls);
    const body = h('div', { class: 'graph-body' });
    screen.append(head, body);
    graph = createGraphEngine(body, controls);
    return screen;
  }
  async function buildGraphAction() {
    const btn = document.getElementById('build-graph-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Building…'; }
    try { await api.post('/graph/build', { clusters: 6 }); await refreshData(); refreshCurrent(); toast('Concept graph built'); }
    catch (e) { toast(e.detail || e.message, true); if (btn) { btn.disabled = false; btn.textContent = 'Build the graph'; } }
  }
  async function rebuildGraph(btn) {
    if (btn) { btn.disabled = true; btn.classList.add('is-busy'); clear(btn); btn.appendChild(document.createTextNode('Rebuilding…')); }
    try { await api.post('/graph/build', { clusters: 6 }); await refreshData(); refreshCurrent(); toast('Concept graph rebuilt'); }
    catch (e) { toast(e.detail || e.message, true); refreshCurrent(); }
  }

  // label-propagation community detection (cheap, dependency-free)
  function detectCommunities(ids, links) {
    const label = {}, adj = {};
    ids.forEach((id) => { label[id] = id; adj[id] = []; });
    links.forEach((l) => { adj[l.s].push(l.t); adj[l.t].push(l.s); });
    for (let it = 0; it < 14; it++) {
      let changed = false;
      for (const id of ids) {
        if (!adj[id].length) continue;
        const counts = {};
        adj[id].forEach((n) => { counts[label[n]] = (counts[label[n]] || 0) + 1; });
        let best = label[id], bestC = -1;
        for (const k in counts) if (counts[k] > bestC || (counts[k] === bestC && k < best)) { bestC = counts[k]; best = k; }
        if (best !== label[id]) { label[id] = best; changed = true; }
      }
      if (!changed) break;
    }
    const groups = [...new Set(ids.map((id) => label[id]))], gi = {};
    groups.forEach((g, i) => (gi[g] = i));
    const out = {}; ids.forEach((id) => (out[id] = gi[label[id]]));
    return { groupOf: out, count: groups.length };
  }

  function createGraphEngine(bodyEl, controlsEl) {
    const concepts = state.nodes.map((n) => ({ id: n.id, name: n.name, label: n.name, cat: n.category || 'general', def: n.definition || 'No definition available.' }));
    const byName = {}; concepts.forEach((c) => (byName[c.name] = c));
    const links = [];
    state.edges.forEach((e) => { const sc = byName[e.source], tc = byName[e.target]; if (!sc || !tc || sc.id === tc.id) return; links.push({ s: sc.id, t: tc.id, rel: e.relationship || 'relates to' }); });
    const degMap = {}; links.forEach((l) => { degMap[l.s] = (degMap[l.s] || 0) + 1; degMap[l.t] = (degMap[l.t] || 0) + 1; });
    const degree = (id) => degMap[id] || 0;
    const maxDeg = Math.max(1, ...concepts.map((c) => degree(c.id)));
    links.forEach((l) => { const d = (degree(l.s) + degree(l.t)) / (2 * maxDeg); l.strength = Math.max(2, Math.min(5, Math.round(2 + d * 3))); });
    const neighbors = (id) => { const o = {}; links.forEach((l) => { if (l.s === id) o[l.t] = l.rel; if (l.t === id) o[l.s] = l.rel; }); return o; };
    const conceptById = (id) => concepts.find((c) => c.id === id);

    // colour strategy: category → community → per-node hue spread
    const cats = [...new Set(concepts.map((c) => c.cat))];
    const meaningfulCats = cats.filter((c) => c && c.toLowerCase() !== 'general');
    let colorKeyOf, hueForKey, legendItems;
    if (meaningfulCats.length >= 2) {
      const hueByCat = {}; let fi = 0; cats.forEach((c) => { hueByCat[c] = KNOWN_HUE[c] != null ? KNOWN_HUE[c] : FALLBACK_HUES[fi++ % FALLBACK_HUES.length]; });
      colorKeyOf = (id) => conceptById(id).cat; hueForKey = (k) => hueByCat[k];
      legendItems = cats.map((c) => ({ key: c, label: catLabel(c), hue: hueByCat[c] }));
    } else {
      const comm = detectCommunities(concepts.map((c) => c.id), links);
      if (comm.count >= 3 && comm.count <= 12) {
        const hues = {}; for (let i = 0; i < comm.count; i++) hues[i] = Math.round((280 + i * (330 / comm.count)) % 360);
        colorKeyOf = (id) => comm.groupOf[id]; hueForKey = (k) => hues[k];
        legendItems = Array.from({ length: comm.count }, (_, i) => ({ key: i, label: 'Cluster ' + (i + 1), hue: hues[i] }));
      } else {
        const order = [...concepts].sort((a, b) => degree(b.id) - degree(a.id) || a.id - b.id);
        const hueByNode = {}; order.forEach((c, i) => (hueByNode[c.id] = Math.round((280 + i * (330 / order.length)) % 360)));
        colorKeyOf = (id) => id; hueForKey = (k) => hueByNode[k];
        legendItems = null; // no meaningful categories
      }
    }
    const hueForId = (id) => hueForKey(colorKeyOf(id));
    const gradIdFor = (id) => 'g-' + cssId(colorKeyOf(id));

    // uniform-relationship detection (suppress repeated edge labels e.g. "co-occurs with")
    const distinctRels = new Set(links.map((l) => l.rel));
    const showEdgeLabels = distinctRels.size > 1;

    // top-degree labels shown by default
    const topSet = new Set([...concepts].sort((a, b) => degree(b.id) - degree(a.id)).slice(0, Math.min(8, concepts.length)).map((c) => c.id));
    const spread = Math.max(1, Math.min(1.7, Math.sqrt(concepts.length / 13)));

    const view = { theta: 0, phi: -0.34, zoom: 1, take: 'organic', hover: null, sel: null };
    let drag = null, lastMoved = false, raf = null, ro = null;
    // per-node nudging: drag a single node to pull it (and its links) out from the pack
    let nodeDrag = null, suppressClick = false, lastProj = {}, lastGm = { sc: 1 };
    const nudge = {};
    // living-graph state: staggered entrance, idle breathing, drag inertia
    const REDUCE = reduceMotion();
    const ENGINE_T0 = performance.now();
    let spinVel = 0, velTheta = 0;
    const seedOf = {}; concepts.forEach((c, i) => { seedOf[c.id] = { s: i * 2.39996, delay: i * 16 }; nudge[c.id] = [0, 0, 0]; });

    const zoomGroup = h('div', { class: 'ctl-group' },
      h('button', { class: 'btn btn--icon', title: 'Zoom out', 'aria-label': 'Zoom out', onClick: () => { view.zoom = Math.max(0.55, view.zoom / 1.18); } }, '−'),
      h('button', { class: 'btn btn--icon', title: 'Zoom in', 'aria-label': 'Zoom in', onClick: () => { view.zoom = Math.min(2.4, view.zoom * 1.18); } }, '+'));
    const segOrganic = h('button', { class: 'btn btn--seg is-active', onClick: () => setTake('organic') }, 'Organic');
    const segRadial = h('button', { class: 'btn btn--seg', onClick: () => setTake('radial') }, 'Radial');
    const recenterBtn = h('button', { class: 'btn btn--icon', title: 'Recenter & reset layout', 'aria-label': 'Recenter and reset layout',
      onClick: () => { concepts.forEach((c) => (nudge[c.id] = [0, 0, 0])); view.theta = 0; view.phi = -0.34; view.zoom = 1; spinVel = 0; view.sel = null; renderPanel(); } },
      icon(['M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21'], 15, 1.9));
    recenterBtn.firstChild.appendChild(s('circle', { cx: 12, cy: 12, r: 3.2 }));
    controlsEl.append(zoomGroup, h('div', { class: 'ctl-group' }, recenterBtn), h('div', { class: 'ctl-group' }, segOrganic, segRadial));
    function setTake(t) { view.take = t; view.sel = null; concepts.forEach((c) => (nudge[c.id] = [0, 0, 0])); segOrganic.classList.toggle('is-active', t === 'organic'); segRadial.classList.toggle('is-active', t === 'radial'); base = base3D(); renderPanel(); }

    const canvas = h('div', { class: 'graph-canvas' });
    const svg = s('svg', { class: 'graph-svg', viewBox: '0 0 880 600', preserveAspectRatio: 'xMidYMid meet' });
    const defs = s('defs'); svg.appendChild(defs);
    const edgeLayer = s('g'), nodeLayer = s('g'); svg.append(edgeLayer, nodeLayer);
    const overlay = h('div', { class: 'graph-overlay' });
    const hint = h('div', { class: 'graph-hint' }, 'drag a node to pull it out · drag space to orbit · scroll to zoom');
    const legend = buildLegend();
    canvas.append(svg, overlay, legend, hint);
    const panel = h('aside', { class: 'graph-panel' });
    bodyEl.append(canvas, panel);

    function rebuildGradients() {
      [...defs.querySelectorAll('radialGradient')].forEach((g) => g.remove());
      const keys = [...new Set(concepts.map((c) => colorKeyOf(c.id)))];
      keys.forEach((k) => { const gs = gradStopsForHue(hueForKey(k), 'g-' + cssId(k)); const rg = s('radialGradient', { id: gs.id, cx: '34%', cy: '30%', r: '72%' }); rg.append(s('stop', { offset: '0%', 'stop-color': gs.hi }), s('stop', { offset: '52%', 'stop-color': gs.mid }), s('stop', { offset: '100%', 'stop-color': gs.lo })); defs.appendChild(rg); });
    }
    function buildLegend() {
      if (!legendItems) return h('div', { class: 'graph-legend' }, h('div', { class: 'graph-legend__item', style: 'color:var(--muted-2)' }, 'Uncategorised concepts · coloured for clarity'));
      const rows = h('div', { class: 'graph-legend__row' });
      legendItems.forEach((it) => rows.appendChild(h('span', { class: 'graph-legend__item' }, h('span', { class: 'graph-legend__swatch', id: 'lg-' + cssId(it.key) }), it.label)));
      const keysRow = showEdgeLabels ? h('div', { class: 'graph-legend__keys' }, h('span', {}, lineSvg(3.4, false), 'strong link'), h('span', {}, lineSvg(1.2, true), 'weak link')) : null;
      return h('div', { class: 'graph-legend' }, rows, keysRow);
    }
    function lineSvg(w, dashed) { const e = s('svg', { width: 26, height: 8 }); e.appendChild(s('line', { x1: 1, y1: 4, x2: 25, y2: 4, stroke: 'var(--mark)', 'stroke-width': w, 'stroke-dasharray': dashed ? '2.5 4.5' : '', 'stroke-linecap': 'round' })); return e; }
    function refreshLegendColors() { if (!legendItems) return; legendItems.forEach((it) => { const sw = document.getElementById('lg-' + cssId(it.key)); if (sw) { sw.style.background = strokeForHue(it.hue); sw.style.border = '1.5px solid ' + strokeForHue(it.hue); } }); }

    function groupsByColor() { const g = {}; concepts.forEach((c) => { const k = colorKeyOf(c.id); (g[k] = g[k] || []).push(c); }); return g; }
    function base3D() {
      const pos = {}, f = spread;
      if (view.take === 'radial') {
        const ord = [...concepts].sort((a, b) => String(colorKeyOf(a.id)).localeCompare(String(colorKeyOf(b.id))) || a.id - b.id);
        ord.forEach((c, i) => { const a = i / ord.length * Math.PI * 2; pos[c.id] = [178 * f * Math.cos(a), i % 2 ? 26 : -26, 178 * f * Math.sin(a)]; });
        return pos;
      }
      const groups = groupsByColor(), keys = Object.keys(groups);
      keys.forEach((k, ci) => {
        const ph = ci / keys.length * Math.PI * 2, cx = 128 * f * Math.cos(ph), cz = 128 * f * Math.sin(ph), cy = ci % 2 ? 74 : -74;
        const grp = groups[k], rr = 38 + grp.length * 4.5;
        grp.forEach((n, i) => { const a = i / Math.max(1, grp.length) * Math.PI * 2 + ci; pos[n.id] = [cx + rr * Math.cos(a), cy + (i % 2 ? 26 : -26), cz + rr * Math.sin(a)]; });
      });
      return pos;
    }
    let base = base3D();

    function project(p) {
      const ct = Math.cos(view.theta), st = Math.sin(view.theta);
      const x = p[0] * ct + p[2] * st, z1 = -p[0] * st + p[2] * ct, y = p[1];
      const cp = Math.cos(view.phi), sp = Math.sin(view.phi);
      const y2 = y * cp - z1 * sp, z2 = y * sp + z1 * cp;
      const P = 560, sc = (P / (P - z2)) * view.zoom;
      return { sx: 440 + x * sc, sy: 300 + y2 * sc, s: sc, z: z2 };
    }
    function gMap() { const w = canvas.clientWidth || 880, ht = canvas.clientHeight || 600; const sc = Math.min(w / 880, ht / 600); return { sc, ox: (w - 880 * sc) / 2, oy: (ht - 600 * sc) / 2 }; }

    const nodeEls = {}, labelEls = {}, edgeEls = [], edgeLabelEls = {};
    function buildElements() {
      clear(edgeLayer); clear(nodeLayer); clear(overlay); edgeEls.length = 0;
      links.forEach((l, i) => {
        const path = s('path', { fill: 'none', 'stroke-linecap': 'round' }); edgeLayer.appendChild(path); edgeEls.push({ path, l, i });
        if (showEdgeLabels) { const lbl = h('div', { class: 'edge-label', style: 'opacity:0;display:none' }, l.rel); overlay.appendChild(lbl); edgeLabelEls[i] = lbl; }
      });
      concepts.forEach((c) => {
        const g = s('g', { style: 'cursor:pointer' });
        const hit = s('circle', { fill: 'transparent' });
        const dot = s('circle', { fill: 'url(#' + gradIdFor(c.id) + ')', stroke: strokeForHue(hueForId(c.id)) });
        g.append(hit, dot);
        g.addEventListener('mouseenter', () => { if (!drag && !nodeDrag) { view.hover = c.id; renderTooltip(); } });
        g.addEventListener('mouseleave', () => { if (view.hover === c.id) { view.hover = null; renderTooltip(); } });
        g.addEventListener('mousedown', (e) => { e.stopPropagation(); nodeDrag = { id: c.id, x: e.clientX, y: e.clientY, moved: false }; spinVel = 0; svg.classList.add('is-grabbing'); });
        g.addEventListener('click', (e) => { e.stopPropagation(); if (suppressClick) { suppressClick = false; return; } view.sel = view.sel === c.id ? null : c.id; renderPanel(); });
        nodeLayer.appendChild(g); nodeEls[c.id] = { g, hit, dot };
        const lbl = h('div', { class: 'node-label' }, c.label); overlay.appendChild(lbl); labelEls[c.id] = lbl;
      });
    }
    const tooltipEl = h('div', { class: 'graph-tooltip', style: 'display:none' }); overlay.appendChild(tooltipEl);

    function frame() {
      const now = performance.now();
      const breathe = !REDUCE && !drag && !nodeDrag;
      const jbase = (c) => {
        const o = nudge[c.id], b0 = base[c.id];
        const b = [b0[0] + o[0], b0[1] + o[1], b0[2] + o[2]];
        if (!breathe) return b;
        const sd = seedOf[c.id].s, amp = 4.5;
        return [b[0] + Math.sin(now * 0.0006 + sd) * amp, b[1] + Math.cos(now * 0.00052 + sd) * amp, b[2] + Math.sin(now * 0.0007 + sd * 1.3) * amp];
      };
      const entOf = (c) => { if (REDUCE) return 1; const e = (now - ENGINE_T0 - seedOf[c.id].delay) / 620; return e <= 0 ? 0 : e >= 1 ? 1 : 1 - Math.pow(1 - e, 3); };
      const proj = {}; concepts.forEach((c) => (proj[c.id] = project(jbase(c))));
      lastProj = proj;
      const zs = concepts.map((c) => proj[c.id].z), zmin = Math.min(...zs), zmax = Math.max(...zs), zr = (zmax - zmin) || 1;
      const dT = (z) => (z - zmin) / zr;
      const gm = gMap(), toPx = (vx, vy) => ({ left: gm.ox + vx * gm.sc, top: gm.oy + vy * gm.sc });
      lastGm = gm;
      const focusId = view.hover || view.sel, nb = focusId ? neighbors(focusId) : {};

      edgeEls.map((e) => ({ e, z: (proj[e.l.s].z + proj[e.l.t].z) / 2 })).sort((a, b) => a.z - b.z).forEach((o) => edgeLayer.appendChild(o.e.path));
      // cap on-focus edge labels to the strongest few so high-degree nodes don't flood the canvas
      let labelSet = null;
      if (focusId && showEdgeLabels) {
        // surface only the meaningful relationship types; the generic "co-occurs
        // with" is suppressed (it was a repetitive wall and adds no information)
        labelSet = new Set(edgeEls.filter((e) => (e.l.s === focusId || e.l.t === focusId) && e.l.rel !== 'co-occurs with')
          .sort((a, b) => b.l.strength - a.l.strength).slice(0, 5).map((e) => e.i));
      }
      edgeEls.forEach(({ path, l, i }) => {
        const a = proj[l.s], b = proj[l.t];
        const active = focusId && (l.s === focusId || l.t === focusId), dimmed = focusId && !active;
        const t = (dT(a.z) + dT(b.z)) / 2, str = l.strength;
        const mx = (a.sx + b.sx) / 2, my = (a.sy + b.sy) / 2, dx = b.sx - a.sx, dy = b.sy - a.sy, len = Math.hypot(dx, dy) || 1, off = 16;
        const cx = mx - dy / len * off, cy = my + dx / len * off, bw = 0.4 + str * 0.45;
        path.setAttribute('d', `M${a.sx.toFixed(1)},${a.sy.toFixed(1)} Q${cx.toFixed(1)},${cy.toFixed(1)} ${b.sx.toFixed(1)},${b.sy.toFixed(1)}`);
        path.setAttribute('stroke', active ? 'var(--mark)' : (dark() ? '#5b5076' : '#c3bbda'));
        path.setAttribute('stroke-width', ((active ? bw + 0.7 : bw) * ((a.s + b.s) / 2)).toFixed(2));
        path.setAttribute('stroke-dasharray', str <= 2 ? '2.5 4.5' : 'none');
        path.setAttribute('opacity', (dimmed ? 0.05 : (active ? 0.92 : Math.min(0.16, (0.05 + 0.028 * str) * (0.6 + 0.5 * t)))).toFixed(3));
        if (showEdgeLabels) { const lbl = edgeLabelEls[i]; if (active && labelSet && labelSet.has(i)) { const px = toPx(cx, cy); lbl.style.display = 'block'; lbl.style.opacity = '1'; lbl.style.left = px.left.toFixed(1) + 'px'; lbl.style.top = px.top.toFixed(1) + 'px'; } else { lbl.style.opacity = '0'; lbl.style.display = 'none'; } }
      });

      const labelFont = Math.max(9.5, 12 * gm.sc).toFixed(1) + 'px';
      [...concepts].sort((a, b) => proj[a.id].z - proj[b.id].z).forEach((c) => {
        const els = nodeEls[c.id]; nodeLayer.appendChild(els.g);
        const p = proj[c.id], deg = degree(c.id), isSel = c.id === view.sel, isFocus = c.id === focusId, isNb = c.id in nb, t = dT(p.z);
        const ent = entOf(c);
        let op = 0.55 + 0.45 * t; if (focusId) op = (isFocus || isNb) ? (0.82 + 0.18 * t) : 0.12;
        op *= ent;
        const r = Math.max(0.5, (10 + Math.min(deg, 6) * 1.9 + (isSel ? 4 : 0) + (isFocus && !isSel ? 2.5 : 0)) * p.s * (0.25 + 0.75 * ent));
        els.g.setAttribute('opacity', op.toFixed(2));
        els.hit.setAttribute('cx', p.sx.toFixed(1)); els.hit.setAttribute('cy', p.sy.toFixed(1)); els.hit.setAttribute('r', (r + 9).toFixed(1));
        els.dot.setAttribute('cx', p.sx.toFixed(1)); els.dot.setAttribute('cy', p.sy.toFixed(1)); els.dot.setAttribute('r', r.toFixed(1));
        els.dot.setAttribute('stroke-width', (isSel ? 2.6 : isFocus ? 2 : 1).toFixed(1));
        els.dot.style.filter = isFocus ? `drop-shadow(0 0 ${(5 * gm.sc).toFixed(1)}px ${strokeForHue(hueForId(c.id))})` : '';
        const lbl = labelEls[c.id], lp = toPx(p.sx, p.sy + r + 11);
        const show = focusId ? (isFocus || isNb) : topSet.has(c.id);
        const lop = focusId ? (isFocus || isNb ? 1 : 0) : (topSet.has(c.id) ? (0.5 + 0.5 * t) : 0);
        lbl.style.left = lp.left.toFixed(1) + 'px'; lbl.style.top = lp.top.toFixed(1) + 'px';
        lbl.style.opacity = (show ? lop : 0).toFixed(2); lbl.style.fontWeight = (isSel || isFocus) ? '600' : '500'; lbl.style.fontSize = labelFont;
      });

      if (view.hover && proj[view.hover]) {
        const p = proj[view.hover], deg = degree(view.hover), rNode = Math.max(5, (10 + Math.min(deg, 6) * 1.9 + 2.5) * p.s) * gm.sc, center = toPx(p.sx, p.sy), above = center.top > 210;
        tooltipEl.style.display = 'block'; tooltipEl.style.left = center.left.toFixed(1) + 'px';
        tooltipEl.style.top = (above ? center.top - rNode - 12 : center.top + rNode + 12).toFixed(1) + 'px';
        tooltipEl.style.transform = above ? 'translate(-50%,-100%)' : 'translate(-50%,0)';
      } else tooltipEl.style.display = 'none';
    }
    function renderTooltip() {
      if (view.hover) {
        const c = conceptById(view.hover), deg = degree(view.hover);
        clear(tooltipEl);
        tooltipEl.append(
          h('div', { class: 'graph-tooltip__name' }, c.name),
          h('div', { class: 'graph-tooltip__cat' }, h('span', { style: 'background:' + strokeForHue(hueForId(c.id)) }), `${deg} connections`),
          h('div', { class: 'graph-tooltip__def' }, c.def));
      }
      frame();
    }
    function dotStyle(id) { const col = strokeForHue(hueForId(id)); return `background:${col};border:1.5px solid ${col}`; }
    function renderPanel() {
      clear(panel);
      const inner = h('div', { class: 'graph-panel__inner' });
      if (view.sel) {
        const c = conceptById(view.sel);
        const conns = links.filter((l) => l.s === view.sel || l.t === view.sel).map((l) => { const otherId = l.s === view.sel ? l.t : l.s; return { o: conceptById(otherId), rel: l.s === view.sel ? l.rel : '← ' + l.rel, str: l.strength }; }).sort((a, b) => b.str - a.str);
        inner.append(
          h('button', { class: 'panel-clear', title: 'Clear selection (Esc)', 'aria-label': 'Clear selection', onClick: () => { view.sel = null; renderPanel(); } }, icon(ICONS.close, 13, 2), 'Clear selection'),
          h('div', { class: 'concept-tag', style: 'color:' + strokeForHue(hueForId(c.id)) }, h('span', { style: 'background:' + strokeForHue(hueForId(c.id)) }), legendItems ? (legendItems.find((x) => x.key === colorKeyOf(c.id)) || {}).label || 'Concept' : 'Concept'),
          h('h2', { class: 'sel-name' }, c.name),
          h('p', { class: 'sel-def' }, c.def),
          h('div', { class: 'panel-label' }, `${conns.length} connections`),
          h('div', { class: 'conn-list' }, conns.slice(0, 12).map((cn) => connCard(cn))),
          h('button', { class: 'ask-concept', onClick: () => { showScreen('chat', true); setTimeout(() => sendChat('Explain ' + c.name + ' and what it connects to.'), 120); } }, icon(ICONS.chat, 16, 1.9), 'Ask about this concept'),
        );
      } else {
        const top = [...concepts].map((c) => ({ c, deg: degree(c.id) })).sort((a, b) => b.deg - a.deg).slice(0, 6);
        inner.append(
          h('h2', { style: 'font-size:20px' }, `${concepts.length} concepts, ${links.length} links`),
          h('p', { class: 'sel-def' }, 'Click any node to read its definition and trace what it connects to. Drag to orbit, scroll to zoom.'),
          h('div', { class: 'panel-label' }, 'Most connected'),
          h('div', { class: 'conn-list' }, top.map((t) => h('button', { class: 'top-node', onClick: () => { view.sel = t.c.id; renderPanel(); } }, h('span', { class: 'conn-card__dot', style: dotStyle(t.c.id) }), h('span', { class: 'top-node__name' }, t.c.name), h('span', { class: 'top-node__deg' }, `${t.deg} links`)))),
        );
      }
      panel.appendChild(inner); frame();
    }
    function connCard(cn) {
      const col = strokeForHue(hueForId(cn.o.id));
      const bars = h('span', { class: 'strength-bars' });
      [1, 2, 3, 4, 5].forEach((n) => bars.appendChild(h('span', { style: `height:${(4 + n * 1.7).toFixed(1)}px;background:${n <= cn.str ? col : 'var(--border)'}` })));
      return h('button', { class: 'conn-card', onClick: () => { view.sel = cn.o.id; renderPanel(); }, onMouseenter: () => { view.hover = cn.o.id; renderTooltip(); }, onMouseleave: () => { if (view.hover === cn.o.id) { view.hover = null; renderTooltip(); } } },
        h('span', { class: 'conn-card__dot', style: dotStyle(cn.o.id) }),
        h('span', { class: 'conn-card__body' }, h('span', { class: 'conn-card__rel' }, cn.rel), h('span', { class: 'conn-card__name' }, cn.o.name)),
        h('span', { class: 'conn-card__strength' }, bars, h('span', { class: 'conn-card__strlabel' }, cn.str >= 4 ? 'strong' : cn.str >= 3 ? 'medium' : 'weak')));
    }

    svg.addEventListener('mousedown', (e) => { drag = { x: e.clientX, y: e.clientY, th: view.theta, ph: view.phi, moved: false }; spinVel = 0; velTheta = 0; svg.classList.add('is-grabbing'); });
    window.addEventListener('mousemove', onMove);
    function onMove(e) {
      if (nodeDrag) {
        const dxs = e.clientX - nodeDrag.x, dys = e.clientY - nodeDrag.y;
        if (Math.abs(dxs) + Math.abs(dys) > 3) nodeDrag.moved = true;
        nodeDrag.x = e.clientX; nodeDrag.y = e.clientY;
        const p = lastProj[nodeDrag.id]; const sc = lastGm.sc || 1; const s = (p && p.s) || 1;
        // screen delta → viewBox → base space (inverse the current orbit rotation)
        const dvx = (dxs / sc) / s, dvy = (dys / sc) / s;
        const ct = Math.cos(view.theta), st = Math.sin(view.theta), cp = Math.cos(view.phi) || 1;
        const o = nudge[nodeDrag.id];
        o[0] += dvx * ct; o[2] += dvx * st; o[1] += dvy / cp;
        return;
      }
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y; if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      const nt = drag.th + dx * 0.008; velTheta = nt - view.theta; view.theta = nt; view.phi = Math.max(-1.15, Math.min(1.15, drag.ph - dy * 0.006));
    }
    window.addEventListener('mouseup', onUp);
    function onUp() {
      if (nodeDrag) { suppressClick = nodeDrag.moved; nodeDrag = null; svg.classList.remove('is-grabbing'); return; }
      if (drag) { lastMoved = drag.moved; spinVel = REDUCE ? 0 : Math.max(-0.06, Math.min(0.06, velTheta)); drag = null; svg.classList.remove('is-grabbing'); }
    }
    svg.addEventListener('click', () => { if (lastMoved) { lastMoved = false; return; } if (view.sel) { view.sel = null; renderPanel(); } });
    canvas.addEventListener('wheel', (e) => { e.preventDefault(); view.zoom = Math.max(0.55, Math.min(2.4, view.zoom * (e.deltaY > 0 ? 0.92 : 1.08))); }, { passive: false });
    function onKey(e) { if (e.key === 'Escape' && view.sel) { view.sel = null; renderPanel(); } }
    window.addEventListener('keydown', onKey);

    let skip = 0;
    function loop() {
      raf = requestAnimationFrame(loop);
      const idle = currentScreen === 'graph' && !drag && !nodeDrag && !view.hover;
      if (Math.abs(spinVel) > 0.0002) {
        view.theta += spinVel; spinVel *= 0.94;            // drag-release inertia, decays
      } else if (idle && !REDUCE) {
        spinVel = 0; skip++; if (skip % 2 === 0) view.theta += view.take === 'radial' ? 0.0038 : 0.006;
      }
      frame();
    }
    ro = new ResizeObserver(() => frame()); ro.observe(canvas);
    rebuildGradients(); buildElements(); refreshLegendColors(); renderPanel(); loop();

    return {
      refreshTheme() { rebuildGradients(); concepts.forEach((c) => nodeEls[c.id].dot.setAttribute('stroke', strokeForHue(hueForId(c.id)))); refreshLegendColors(); renderPanel(); renderTooltip(); },
      dispose() { if (raf) cancelAnimationFrame(raf); if (ro) ro.disconnect(); window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); window.removeEventListener('keydown', onKey); },
    };
  }

  // ── Chat ────────────────────────────────────────────────────────────────────
  let chatStreamEl = null, chatInputEl = null;
  const CHAT_SUGGESTIONS = ['What is in my library?', 'Summarize the key ideas.', 'What connects these concepts?'];
  function buildChat() {
    const screen = h('div', { class: 'screen screen--flush' });
    screen.appendChild(h('div', { class: 'chat-head' }, h('div', { class: 'chat-narrow' }, h('div', { class: 'section__kicker' }, 'Ask'), h('h1', {}, 'Cited answers from your documents'))));
    chatStreamEl = h('div', { class: 'chat-stream' });
    screen.appendChild(h('div', { class: 'chat-scroll', id: 'chat-scroll' }, chatStreamEl));
    chatInputEl = h('input', { placeholder: 'Ask anything about your documents…', 'aria-label': 'Ask a question about your documents', onKeydown: (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); } } });
    screen.appendChild(h('div', { class: 'chat-foot' }, h('div', { class: 'chat-narrow' },
      h('div', { class: 'chat-input-bar' }, chatInputEl, h('button', { class: 'chat-send', 'aria-label': 'Send message', onClick: () => sendChat() }, icon(ICONS.arrow, 19, 2.1))),
      h('div', { class: 'chat-disclaimer' }, 'Answers are grounded only in your indexed sources · nothing leaves this machine'))));
    drawChat();
    return screen;
  }
  function chatEmptyState() {
    return h('div', { class: 'chat-empty' },
      h('div', { class: 'chat-empty__mark' }),
      h('h2', {}, 'Ask your library anything'),
      h('p', {}, 'Every answer is pulled straight from your indexed documents, with citations. Nothing leaves your machine.'),
      h('div', { class: 'suggest-row' }, CHAT_SUGGESTIONS.map((t) => h('button', { class: 'btn btn--chip', onClick: () => sendChat(t) }, t))));
  }
  function drawChat() {
    if (!chatStreamEl) return; clear(chatStreamEl);
    if (!state.chat.length && !state.thinking) {
      chatStreamEl.appendChild(chatEmptyState());
    } else {
      state.chat.forEach((m) => chatStreamEl.appendChild(m.role === 'user' ? userMsg(m) : assistantMsg(m)));
      if (state.thinking) chatStreamEl.appendChild(thinkingMsg());
      const last = chatStreamEl.lastElementChild; if (last) last.classList.add('msg-in'); // only the newest animates
    }
    const sc = document.getElementById('chat-scroll'); if (sc) sc.scrollTop = sc.scrollHeight;
  }
  function userMsg(m) { return h('div', { class: 'msg--user' }, h('div', {}, m.text)); }
  function assistantMsg(m) {
    const body = h('div', { class: 'msg__body' });
    if (m.note) body.appendChild(h('div', { class: 'msg__note' }, m.note));
    const text = h('div', { class: 'msg__text' });
    splitRuns(m.text).forEach((r) => text.appendChild(r.isCite ? h('sup', { class: 'cite-sup' }, String(r.n)) : document.createTextNode(r.t)));
    body.appendChild(text);
    if (m.cites && m.cites.length) { const cites = h('div', { class: 'cites' }, h('div', { class: 'panel-label' }, 'Sources')); m.cites.forEach((c) => cites.appendChild(citeCard(c))); body.appendChild(cites); }
    return h('div', { class: 'msg--assistant' }, h('div', { class: 'assistant-avatar' }), body);
  }
  function citeCard(c) {
    return h('div', { class: 'cite-card' }, h('span', { class: 'cite-card__n' }, String(c.n)),
      h('span', { class: 'cite-card__body' }, h('span', { class: 'cite-card__head' }, h('span', { class: 'cite-card__title' }, c.title), h('span', { class: 'cite-card__meta' }, c.meta)), h('span', { class: 'cite-card__quote' }, c.quote)));
  }
  function thinkingMsg() { return h('div', { class: 'thinking' }, h('div', { class: 'assistant-avatar' }), h('div', { class: 'thinking__body' }, 'Searching your library', h('span', { class: 'thinking__dots' }, h('span', {}), h('span', {}), h('span', {})))); }
  function splitRuns(text) { return String(text).split(/(\[\d+\])/g).filter((p) => p !== '').map((p) => { const m = p.match(/^\[(\d+)\]$/); return m ? { isCite: true, n: +m[1] } : { isCite: false, t: p }; }); }
  function hitToCite(hit, n) { const meta = [hit.source_author, hit.location].filter(Boolean).join(' · '); const q = (hit.text || '').trim(); return { n, title: hit.source_title || 'Source', meta, quote: q.length > 220 ? q.slice(0, 220) + '…' : q }; }
  async function sendChat(q) {
    const text = (q !== undefined ? q : (chatInputEl ? chatInputEl.value : '')).trim();
    if (!text || state.thinking) return;
    state.chat.push({ role: 'user', text }); if (chatInputEl) chatInputEl.value = '';
    state.thinking = true; drawChat();
    try {
      const r = await api.post('/chat', { query_text: text, limit: 6 });
      state.chat.push({ role: 'assistant', text: r.answer || '', cites: (r.sources || []).map((hit, i) => hitToCite(hit, i + 1)) });
    } catch (e) {
      if (e.status === 503) {
        try {
          const hits = await api.post('/search', { query_text: text, limit: 6 });
          const cites = (hits || []).map((hit, i) => hitToCite(hit, i + 1));
          state.chat.push({ role: 'assistant', note: 'No chat model is configured, so here are the most relevant passages from your library (retrieval only).', text: cites.length ? 'Top matches for your question:' : 'No matching passages found in your indexed sources yet.', cites });
        } catch (e2) { state.chat.push({ role: 'assistant', text: 'Search failed: ' + (e2.detail || e2.message) }); }
      } else state.chat.push({ role: 'assistant', text: 'Something went wrong: ' + (e.detail || e.message) });
    } finally { state.thinking = false; drawChat(); }
  }

  // ── boot ──────────────────────────────────────────────────────────────────
  document.documentElement.setAttribute('data-theme', state.theme);
  render();
})();
