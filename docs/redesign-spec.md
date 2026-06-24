# Psyche — Signature UX Redesign Spec

**Goal:** Take the clean-but-generic frontend to a distinctive, memorable, joy-to-return-to
product. Signature restraint (stays a fast daily tool), the **living knowledge graph** as the
hero, a **cinematic scroll-driven landing**, and a **cohesive motion language** across the app.
Colors and type system are unchanged (Newsreader / Hanken Grotesk / JetBrains Mono, purple).

**Tech:** vanilla JS frontend + vendored, offline motion libs in `web/static/vendor/`:
GSAP 3.12.5 + ScrollTrigger (scroll-scrubbed timelines), Lenis 1.1 (smooth scroll). No CDN at
runtime; all local. Three.js intentionally NOT used (keep it light; the graph stays SVG).

**Non-negotiables:** every motion has a `prefers-reduced-motion` fallback; content is visible by
default (reveals enhance, never gate); 60fps target; keyboard + a11y preserved; mobile works.

---

## North star concept — "Your mind, mapped."

One idea ties it together: **scattered documents become a connected mind.** The landing *narrates*
that transformation as you scroll; the app *is* it. The graph is the thing people screenshot.

---

## 1. Landing — scroll-driven "second brain forms"

A pinned, scroll-scrubbed GSAP/ScrollTrigger sequence. Lenis drives buttery scroll. Editorial
hero stays; the cinematic sequence is the new middle.

**Beats (each = a pinned scene, scrubbed by scroll):**
1. **Hero** — keep the Newsreader headline. Add an ambient living-graph field drifting behind/beside
   it (faint nodes, slow orbit) — a teaser of the hero. Subtle entrance on load.
2. **Ingest** — document cards (PDF/EPUB/MD…) slide/stack in. Headline: "Drop in your library."
3. **Embed** — cards dissolve into a scatter of points (chunks). "Everything, chunked & embedded — locally."
4. **Link** — points drift into clusters; edges draw between them (animated stroke-dashoffset). "Ideas find each other."
5. **Recall (graph)** — a glowing concept graph resolves; a couple of labels fade in. "A mind you can query." → CTA.
6. **One memory, every agent** — agent glyphs (Claude Code / Codex / Gemini / Antigravity) connect to a shared core. "One memory. Every agent. Billed $0."

Implementation: one `<canvas>` or reused SVG layer whose particle/edge state is interpolated by a
single GSAP timeline bound to ScrollTrigger (scrub). Reduced-motion / no-JS: render the final
"Recall" graph state statically with the same copy, no pinning.

## 2. Living knowledge graph (HERO)

Upgrade the existing SVG orbit engine (`createGraphEngine`) — keep data wiring & layouts:
- **Ambient life:** very subtle per-node positional jitter (seeded sine) so the graph "breathes" when idle, on top of the existing slow auto-orbit. Pauses on hover/drag and under reduced-motion.
- **Depth:** stronger far/near contrast — far nodes more transparent + slight blur (SVG `filter` or opacity already present; push it), near nodes crisper. DoF feel.
- **Glow:** hovered/selected node gets a soft outer glow (SVG blur halo) in its category hue.
- **Entrance:** on first graph view, nodes stagger-scale in from center and edges draw in (GSAP), ~600ms, once.
- **Focus transition:** selecting a node eases the orbit slightly toward it and animates dimming (GSAP tween of theta/opacity) instead of instant snap.
- **Drag inertia:** releasing a drag keeps a little spin that decays (momentum), Lenis-like.
- **Edge pulse (restraint):** on a selected node, a faint pulse travels its active edges (animated dash), low-key.
Performance: all within the existing rAF loop; effects capped; no layout thrash.

## 3. Cohesive motion language (micro-interactions)

Define motion tokens (durations, eases) in CSS/JS and apply consistently:
- **Tabs:** keep directional screen slide; drive the tab-pill with a spring; content stagger-reveals its header → body.
- **Buttons:** existing depth + a subtle press spring; primary CTA a gentle idle shimmer (very restrained, reduced-motion off).
- **Cards / rows / agent cards / source rows / cite cards:** hover lift (translateY + shadow), clear focus rings.
- **Chat:** assistant answer + citations stagger in; thinking dots refined; send button springs.
- **Upload:** drop → row pops in; on "indexed", a satisfying check/pop; dropzone reacts to drag with scale/glow.
- **Toasts:** spring up, auto-dismiss.
- **Empty/loading states:** skeleton shimmer where data loads.

## 4. Accessibility & polish (ship gate)

- `aria-label` on every icon-only button (theme, zoom, remove, send, rebuild).
- Visible focus rings on all interactive elements; tab order sane; graph nodes reachable (list fallback already exists via "Most connected").
- Contrast re-checked in light + dark for any changed text.
- `prefers-reduced-motion`: Lenis disabled, ScrollTrigger scrubs jump to end-states, ambient/jitter/shimmer off, transitions → instant/crossfade.
- Mobile: landing sequence degrades to stacked static scenes; app tabs already responsive.

---

## Build order (each stage verified in-browser before the next)

0. Vendor GSAP/ScrollTrigger/Lenis (done). Branch `feat/signature-ux-redesign`.
1. Motion foundation: load libs, motion tokens, Lenis on landing, reduced-motion infra.
2. Landing scrollytelling sequence (beats 1–6).
3. Living-graph upgrade (ambient/depth/glow/entrance/focus/inertia).
4. Cohesive micro-interactions across setup/upload/graph/chat + global components.
5. A11y + reduced-motion + mobile + cross-surface polish; full verification pass.
6. Commit; push `feat/signature-ux-redesign`; open PR.

## Out of scope (tracked separately)
- The ship-readiness QA findings (security/packaging/MCP-wiring) from the parallel audit — folded
  in as a separate pass before final launch.
