# Psyche — Loom Walkthrough Script

A scene-by-scene recording plan for a polished, portfolio-grade product walkthrough.
**Target length:** 3:00–4:00. **Tone:** calm, confident, builder-to-builder — let the work speak.
**Pairs with:** `showcase/index.html` (the visual backbone) + live terminal demos.

---

## 0. Pre-flight (do this before you hit record)

**Windows & layout**
- Serve the showcase locally so it's crisp and clickable:
  `python3 -m http.server 8913 --directory showcase` → open `http://localhost:8913`.
- Browser at 1280×720 (or 1440×810), 100% zoom, **light/clean OS theme**, hide bookmarks bar.
- A terminal with a **large font (≥18pt)**, minimal prompt, dark theme (matches the site's panels).
- Close Slack/Mail/notifications. Turn on Do Not Disturb.

**Loom settings**
- Screen + small cam bubble bottom-right (optional but good for portfolio — it's *you* building this).
- 1080p, 30fps. Record system audio OFF unless a demo needs it.

**Prep the live demo data (so terminals aren't empty)**
- Have a project with some memories already captured, OR seed a demo DB first:
  - `psyche mem stats` should show a non-zero token ledger.
  - `psyche mem outcomes` should show a few good/bad rows (run a couple of real Claude Code sessions first, or seed).
- Pre-type long commands in a scratch file to paste — no live typos on camera.

**B-roll to capture separately (15–20s clips, no narration — you'll cut these in)**
- The hero section scrolling slowly.
- A Claude Code session **starting** with the SessionStart hook injecting facts.
- `psyche mem outcomes` output rendering.

---

## 1. Cold open — the hook (0:00–0:20)

| | |
|---|---|
| **Visual** | Hero section of the showcase, full screen. Slow scroll from headline to the inject terminal card. |
| **Action** | Let the "One memory across **every** AI agent" headline sit for a beat. Hover the terminal card. |

> "Every AI coding assistant has the same problem: amnesia. Every new session, it re-reads your files, re-asks your preferences, repeats mistakes it already made — and you pay for all of it, in tokens.
>
> This is Psyche. It gives every assistant you use one shared, persistent memory — and it runs entirely on your machine."

---

## 2. The problem, quantified (0:20–0:35)

| | |
|---|---|
| **Visual** | Scroll to the dark "Your AI assistant has amnesia" band. Let the **10k–40k** stat land. |
| **Action** | Pause on the stat. |

> "On a recurring project, that re-derivation costs ten to forty thousand redundant tokens — every single session. Psyche ends that tax."

---

## 3. The core idea — one brain, cross-agent (0:35–1:00)

| | |
|---|---|
| **Visual** | Scroll through the before/after comparison table, then the cross-agent hub (Claude Code · Codex · Antigravity · Cursor → one local store). |
| **Action** | Trace the arrow down to "One shared local store." |

> "A preference you state in Codex is known to Claude Code. A lesson learned in one session follows you to the next. It's a mem0-class memory engine — extraction, dedup, hybrid retrieval, entity links — shared by every agent, and it costs zero dollars to run because nothing calls an API and nothing leaves your disk."

---

## 4. LIVE DEMO — it actually works (1:00–1:55)  ⭐ the centerpiece

Switch to the **terminal**. This is the proof; don't rush it.

**4a. Memory is real and local**
```bash
psyche mem stats
```
> "Here's the memory store on my machine. Standing facts, and a token ledger — measured straight from my Claude Code transcripts. Notice I'm not claiming a headline 'saves you X percent' — it reports the honest, attributable number."

**4b. Cross-agent injection (B-roll or live)**
Show a Claude Code session starting; the SessionStart hook injects standing facts.
> "When I start a session, the harness injects my standing facts automatically — zero tool calls, zero model turns spent on memory. The model just *knows* my conventions."

**4c. The new part — it learns**
```bash
psyche mem outcomes
```
> "And this is what I shipped most recently: an experiential-learning loop. Psyche records which facts were present when a session went well versus badly. Here are the top facts by win-rate — and here are forget candidates, the ones that correlate with bad outcomes."

**4d. Permissioned forgetting**
```bash
psyche mem forget "tabs over spaces"
psyche mem review
```
> "If a memory is stale — something I used to do but don't anymore — I can retire it. With permission, always. That's the difference between learning and just hoarding context. And I'm honest in the UI: these are observed signals, not causal proof, and they don't silently re-rank your memory yet."

---

## 5. How it's built (1:55–2:35)

| | |
|---|---|
| **Visual** | Back to the showcase. Scroll the lifecycle-hooks timeline, then the dark architecture pipeline. |
| **Action** | Point at the **NEW** "Stop — capture incrementally" node. Then sweep across the 6-stage pipeline. |

> "Under the hood: lifecycle hooks do everything. Inject at session start, search on each prompt, and — the piece I just added — capture facts *mid-session*, so your learnings survive even if you walk away for days and never formally end the session.
>
> And the whole pipeline is local: ingest your notes and books, chunk them, embed with on-device ONNX models, retrieve with reciprocal rank fusion, rerank with a CPU cross-encoder, and serve it all over the Model Context Protocol. Nothing is uploaded. Every answer is cited back to a file, chapter, or page."

---

## 6. The breadth (2:35–3:00)

| | |
|---|---|
| **Visual** | Scroll quickly through the memory hierarchy, the guidance engine + GraphRAG cards, and the privacy pillars. |

> "It's more than memory — there's a five-tier memory hierarchy, a guidance engine that turns your goals into tracked experiments grounded in your own notes, and a concept graph over your whole collection. All local-first, all private."

---

## 7. Close + CTA (3:00–3:20)

| | |
|---|---|
| **Visual** | Quickstart section — the two `npx` commands. End on the hero or the GitHub button. |
| **Action** | Hover "Star on GitHub." |

> "Two commands to get started. It's open source, MIT-licensed, with a hundred and sixty tests behind it. Link's in the description — I'd love a star, and I'm happy to talk through how any of it works."

*(End card: Psyche logo + GitHub URL, hold 3s.)*

---

## Editing notes
- **Music:** soft, low ambient — under -20dB. Cut it during the live terminal demo so the work is the focus.
- **Captions:** burn in subtitles — most portfolio viewers watch muted first.
- **Pace:** scenes 1–3 can be brisk; **slow down for scene 4** (the demo is the credibility).
- **Cut to a 45s teaser** for LinkedIn/X: cold open (scene 1) → 15s of `psyche mem outcomes` (scene 4c) → CTA (scene 7).
- **Thumbnail:** the hero headline on the warm paper background reads great as a still.

## One-line description (for the Loom/post)
> Psyche — a local-first, cross-agent memory engine for AI assistants. One shared brain across Claude Code, Codex & Antigravity that's private, $0 to run, and learns from outcomes. Open source.
