# Deep Series Documentary Lite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade deep-series generation from static text-card narration to a lightweight documentary-style package while keeping the current local pipeline.

**Architecture:** Keep the daily-brief flow untouched. Add focused helpers in `deep_series.py` for documentary prompts, publish metadata, cover image output, short visual slide planning, and slide durations. Cover behavior with targeted `test_deep_series.py` tests.

**Tech Stack:** Python unittest, Pillow image generation, existing LLM/TTS/FFmpeg pipeline.

---

### Task 1: Lock Documentary Metadata Behavior

**Files:**
- Modify: `test_deep_series.py`
- Modify: `deep_series.py`

- [ ] Add tests that publish metadata includes a short Bilibili title, cover text, cover prompt, and generated cover path.
- [ ] Implement minimal normalization and cover image generation in `deep_series.py`.
- [ ] Run `py -3 -m unittest test_deep_series.py -v`.

### Task 2: Lock Documentary Script Package Behavior

**Files:**
- Modify: `test_deep_series.py`
- Modify: `deep_series.py`

- [ ] Add tests that the research/script prompts require trend-first documentary structure and no course-style opening.
- [ ] Add a documentary package artifact path to `run_episode_pipeline()`.
- [ ] Run `py -3 -m unittest test_deep_series.py -v`.

### Task 3: Lock 2-4 Second Visual Rhythm

**Files:**
- Modify: `test_deep_series.py`
- Modify: `deep_series.py`

- [ ] Add tests that long dialogue segments are split into multiple visual cards with matching durations.
- [ ] Implement visual slide plan generation and duration sidecar loading.
- [ ] Update deep text cards to dark iOS documentary styling.
- [ ] Run `py -3 -m unittest test_deep_series.py -v`.

### Self-Review

- Scope stays inside deep-series generation and tests.
- No daily-brief behavior changes.
- No external B-roll provider or AI video API is introduced.
