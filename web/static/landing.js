/* Psyche landing — the cinematic "scattered documents become a connected mind"
   scroll sequence. GSAP + ScrollTrigger (scrubbed) + Lenis (smooth scroll),
   all vendored/offline. Degrades to a static, fully-visible page under
   prefers-reduced-motion or if the libs are unavailable. app.js calls
   window.PsycheLanding.init() after mounting the landing and .destroy() on exit. */
(() => {
  'use strict';

  const TWO_PI = Math.PI * 2;
  const HUES = [262, 286, 246, 320, 210]; // purple-leaning cluster hues
  let S = null; // live instance state

  const reduce = () => !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const libsReady = () => window.gsap && window.ScrollTrigger && window.Lenis;
  const ease = (t) => 1 - Math.pow(1 - Math.min(1, Math.max(0, t)), 3);
  const lerp = (a, b, t) => a + (b - a) * t;

  function fit(canvas) {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const r = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.round(r.width * dpr));
    canvas.height = Math.max(1, Math.round(r.height * dpr));
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    canvas._w = r.width; canvas._h = r.height;
    return ctx;
  }

  // deterministic per-index pseudo-random in [0,1)
  function rnd(seed) { const x = Math.sin(seed * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); }

  // ── scrolly particle layouts (docs → scatter → cluster → recall) ───────────
  function buildPoints(W, H) {
    const N = 70, CLUSTERS = 5, pts = [];
    const cx = W * 0.5, cy = H * 0.46;
    const cCenters = [];
    for (let c = 0; c < CLUSTERS; c++) {
      const a = -Math.PI / 2 + c / CLUSTERS * TWO_PI;
      cCenters.push([cx + Math.cos(a) * Math.min(W, H) * 0.26, cy + Math.sin(a) * Math.min(W, H) * 0.22]);
    }
    const cols = 4, perCol = Math.ceil(N / cols);
    for (let i = 0; i < N; i++) {
      const cl = i % CLUSTERS;
      // L0 docs: columns spread wide apart (loose "stacks" of pages), with a
      // little horizontal jitter so they read as scattered, not rigid bars.
      const col = i % cols, row = Math.floor(i / cols);
      const docs = [W * (0.16 + col * 0.225) + (rnd(i) - 0.5) * W * 0.04, H * (0.22 + (row / perCol) * 0.54)];
      // L1 scatter: embedding field
      const scatter = [W * (0.18 + rnd(i + 1) * 0.64), H * (0.18 + rnd(i + 7) * 0.6)];
      // L2 cluster + L3 recall (tighter)
      const ca = rnd(i + 3) * TWO_PI, cr = (0.3 + rnd(i + 5) * 0.7);
      const spread = Math.min(W, H) * 0.12;
      const cluster = [cCenters[cl][0] + Math.cos(ca) * cr * spread, cCenters[cl][1] + Math.sin(ca) * cr * spread];
      const recall = [lerp(cluster[0], cx, 0.12), lerp(cluster[1], cy, 0.12)];
      pts.push({ cl, hue: HUES[cl], r: 3.4 + rnd(i + 9) * 2.8, L: [docs, scatter, cluster, recall] });
    }
    return pts;
  }

  function drawScrolly(canvas, p) {
    const ctx = canvas._ctx || (canvas._ctx = canvas.getContext('2d'));
    const W = canvas._w, H = canvas._h;
    if (!W) return;
    if (!canvas._pts) canvas._pts = buildPoints(W, H);
    const pts = canvas._pts;
    ctx.clearRect(0, 0, W, H);

    const idx = Math.min(3, Math.floor(p * 4));
    const frac = ease(p * 4 - idx);
    const from = idx === 0 ? 0 : idx - 1, to = idx;
    const pos = pts.map((pt) => {
      const a = pt.L[from], b = pt.L[to];
      return [lerp(a[0], b[0], frac), lerp(a[1], b[1], frac)];
    });

    // edges appear in Link (idx 2) and strengthen in Recall (idx 3)
    const edgeAlpha = idx >= 3 ? 1 : idx === 2 ? frac : 0;
    if (edgeAlpha > 0.01) {
      ctx.lineWidth = 1;
      for (let i = 0; i < pts.length; i++) {
        for (let j = i + 1; j < pts.length; j++) {
          if (pts[i].cl !== pts[j].cl) continue;
          const dx = pos[i][0] - pos[j][0], dy = pos[i][1] - pos[j][1];
          const d = Math.hypot(dx, dy);
          if (d > Math.min(W, H) * 0.16) continue;
          ctx.strokeStyle = `hsla(${pts[i].hue},60%,60%,${(edgeAlpha * (1 - d / (Math.min(W, H) * 0.16)) * 0.4).toFixed(3)})`;
          ctx.beginPath(); ctx.moveTo(pos[i][0], pos[i][1]); ctx.lineTo(pos[j][0], pos[j][1]); ctx.stroke();
        }
      }
    }

    const glow = idx >= 3 ? frac : 0;
    for (let i = 0; i < pts.length; i++) {
      const pt = pts[i], r = pt.r * (1 + glow * 0.5);
      if (glow > 0.02) {
        ctx.shadowColor = `hsla(${pt.hue},70%,62%,${(glow * 0.9).toFixed(2)})`;
        ctx.shadowBlur = 10 * glow;
      } else ctx.shadowBlur = 0;
      ctx.fillStyle = `hsl(${pt.hue},62%,${idx === 0 ? 70 : 60}%)`;
      ctx.beginPath(); ctx.arc(pos[i][0], pos[i][1], r, 0, TWO_PI); ctx.fill();
    }
    ctx.shadowBlur = 0;
  }

  function updateCaps(caps, p) {
    const idx = Math.min(caps.length - 1, Math.floor(p * 4));
    caps.forEach((c, i) => setCap(c, i === idx));
  }
  function setCap(el, on) {
    if (!el) return;
    el.style.transition = 'opacity .45s ease, transform .45s cubic-bezier(.2,.7,.2,1)';
    el.style.opacity = on ? '1' : '0';
    el.style.transform = `translate(-50%, ${on ? '0' : '12px'})`;
  }

  // ── ambient hero field (slow drifting nodes + faint links) ─────────────────
  function startHero(canvas, isReduced) {
    const ctx = fit(canvas);
    const W0 = canvas._w, H0 = canvas._h, N = 18;
    const nodes = [];
    for (let i = 0; i < N; i++) {
      nodes.push({ x: rnd(i + 2) * W0, y: rnd(i + 4) * H0, vx: (rnd(i + 6) - 0.5) * 0.18, vy: (rnd(i + 8) - 0.5) * 0.18, r: 2 + rnd(i) * 3, hue: HUES[i % HUES.length] });
    }
    const draw = () => {
      const W = canvas._w, H = canvas._h;
      ctx.clearRect(0, 0, W, H);
      for (let i = 0; i < N; i++) for (let j = i + 1; j < N; j++) {
        const dx = nodes[i].x - nodes[j].x, dy = nodes[i].y - nodes[j].y, d = Math.hypot(dx, dy);
        if (d < 150) { ctx.strokeStyle = `hsla(262,55%,60%,${(0.10 * (1 - d / 150)).toFixed(3)})`; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(nodes[i].x, nodes[i].y); ctx.lineTo(nodes[j].x, nodes[j].y); ctx.stroke(); }
      }
      for (const n of nodes) { ctx.fillStyle = `hsla(${n.hue},60%,62%,0.28)`; ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, TWO_PI); ctx.fill(); }
    };
    if (isReduced) { draw(); return null; }
    const step = () => {
      const W = canvas._w, H = canvas._h;
      for (const n of nodes) { n.x += n.vx; n.y += n.vy; if (n.x < 0 || n.x > W) n.vx *= -1; if (n.y < 0 || n.y > H) n.vy *= -1; }
      draw();
      S && S.rafs.push(requestAnimationFrame(step));
    };
    return requestAnimationFrame(step);
  }

  window.PsycheLanding = {
    init() {
      this.destroy();
      S = { rafs: [], triggers: [], lenis: null, ticker: null, onResize: null };
      const isReduced = reduce();
      const heroCanvas = document.getElementById('lh-hero-canvas');
      const scrollyCanvas = document.getElementById('lh-canvas');
      const caps = [0, 1, 2, 3].map((i) => document.getElementById('lh-cap-' + i)).filter(Boolean);

      if (heroCanvas) { const id = startHero(heroCanvas, isReduced); if (id) S.rafs.push(id); }

      // Fallback: no motion libs or reduced-motion → reveal everything, show the
      // final "recall" scene. Content is never gated on scroll.
      if (isReduced || !libsReady()) {
        document.querySelectorAll('.reveal').forEach((e) => e.classList.add('is-in'));
        caps.forEach((c, i) => setCap(c, i === caps.length - 1));
        if (scrollyCanvas) { fit(scrollyCanvas); drawScrolly(scrollyCanvas, 1); }
        return;
      }

      gsap.registerPlugin(ScrollTrigger);
      const lenis = new Lenis({ duration: 1.05, smoothWheel: true, wheelMultiplier: 1 });
      S.lenis = lenis;
      window.__lenis = lenis; // debug handle for programmatic scrolling/verification
      lenis.on('scroll', ScrollTrigger.update);
      const ticker = (t) => lenis.raf(t * 1000);
      gsap.ticker.add(ticker); gsap.ticker.lagSmoothing(0); S.ticker = ticker;

      document.querySelectorAll('.reveal').forEach((el) => {
        S.triggers.push(ScrollTrigger.create({ trigger: el, start: 'top 86%', once: true, onEnter: () => el.classList.add('is-in') }));
      });

      const scrolly = document.getElementById('lh-scrolly');
      const stage = document.getElementById('lh-stage');
      if (scrolly && stage && scrollyCanvas) {
        fit(scrollyCanvas);
        S.triggers.push(ScrollTrigger.create({
          trigger: scrolly, start: 'top top', end: '+=320%', pin: stage, scrub: 0.6,
          onUpdate: (self) => { drawScrolly(scrollyCanvas, self.progress); updateCaps(caps, self.progress); },
        }));
        drawScrolly(scrollyCanvas, 0); updateCaps(caps, 0);
      }

      const onResize = () => { if (heroCanvas) fit(heroCanvas); if (scrollyCanvas) { fit(scrollyCanvas); scrollyCanvas._pts = null; } ScrollTrigger.refresh(); };
      window.addEventListener('resize', onResize); S.onResize = onResize;
    },

    destroy() {
      if (!S) return;
      S.rafs.forEach((id) => cancelAnimationFrame(id));
      S.triggers.forEach((t) => t && t.kill && t.kill());
      if (S.ticker && window.gsap) gsap.ticker.remove(S.ticker);
      if (S.lenis) { try { S.lenis.destroy(); } catch (e) {} }
      if (S.onResize) window.removeEventListener('resize', S.onResize);
      S = null;
    },
  };
})();
