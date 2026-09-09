# ADK Parser Interface Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an ADK RUS-inspired operational interface for the Avito parser while preserving every existing task-management and settings workflow.

**Architecture:** Keep FastAPI routes and scraper behavior untouched. Rebuild only the static document structure and CSS, then make narrow presentation additions in `app.js` that derive statistics from the already loaded task list. A small static contract test protects the element ids and endpoint references the existing client behavior requires.

**Tech Stack:** FastAPI static files, vanilla HTML/CSS/JavaScript, Python standard-library `unittest`, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-09-07-adk-parser-interface-design.md`

## Global Constraints

- Keep all existing parser APIs and task-state behavior unchanged.
- Preserve existing JavaScript element ids used for task creation, proxy settings, captcha keys, and spfa.ru keys.
- Use a dark industrial palette, orange primary actions, a five-pixel spacing rhythm, and restrained radii.
- Keep all task actions keyboard accessible and usable on narrow windows.
- Do not add dependencies or change backend routes.

---

### Task 1: Establish the frontend contract test

**Files:**
- Create: `tests/test_frontend_contract.py`
- Modify: none

**Interfaces:**
- Consumes: static source files at `app/static/index.html` and `app/static/app.js`.
- Produces: `FrontendContractTests`, which verifies retained DOM ids and client API routes.

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):
    def test_operational_workspace_landmarks_are_present(self):
        html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
        self.assertIn('class="app-shell"', html)
        self.assertIn('id="taskStats"', html)
        self.assertIn('id="tasksList"', html)

    def test_existing_form_controls_and_routes_remain_available(self):
        html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
        script = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
        for element_id in ("linksList", "addLinkBtn", "limitPerLink", "onlyRegion", "mergeFile", "startBtn"):
            self.assertIn(f'id="{element_id}"', html)
        for route in ("/api/tasks", "/api/proxies", "/api/captcha-key", "/api/cookie-service-key"):
            self.assertIn(route, script)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv\Scripts\python.exe -m unittest tests.test_frontend_contract -v`

Expected: the operational workspace test fails because `app-shell` and `taskStats` do not exist yet.

- [ ] **Step 3: Do not change production files in this task**

The test intentionally remains red until Task 2 adds the new workspace landmarks.

- [ ] **Step 4: Record the red result**

Run: `venv\Scripts\python.exe -m unittest tests.test_frontend_contract -v`

Expected: the same targeted failures remain until implementation begins.

- [ ] **Step 5: No commit**

This project is not a Git repository. Keep the test alongside the implementation changes for the final verification run.

### Task 2: Build the ADK operational workspace

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/style.css`
- Modify: `app/static/app.js`
- Create: `app/static/adk-rus-logo.svg` or `app/static/adk-rus-logo.png`

**Interfaces:**
- Consumes: existing HTML element ids and `renderTasks(tasks)` in `app.js`.
- Produces: `.app-shell`, `#taskStats`, `.control-rail`, `.workspace`, and `renderTaskStats(tasks)`.

- [ ] **Step 1: Run the red contract test before implementation**

Run: `venv\Scripts\python.exe -m unittest tests.test_frontend_contract -v`

Expected: FAIL because the new workspace landmarks are absent.

- [ ] **Step 2: Restructure the static document without changing ids**

In `app/static/index.html`, wrap the UI in an `.app-shell`, add a dark top bar with the locally stored ADK RUS logo, put the existing form inside a `.control-rail`, and put `#taskStats` and `#tasksList` inside `.workspace`. Preserve every existing `id` in the form and all `<details>` settings blocks.

```html
<main class="app-shell">
  <header class="topbar">...</header>
  <div class="app-layout">
    <aside class="control-rail">...existing form ids...</aside>
    <section class="workspace">
      <div id="taskStats" class="task-stats"></div>
      <div id="tasksList" class="tasks-list"></div>
    </section>
  </div>
</main>
```

- [ ] **Step 3: Replace the presentation layer in CSS**

In `app/static/style.css`, define design tokens for graphite backgrounds, white text, orange `--accent`, muted gray text, success green, warning amber, and danger red. Style the top bar, side rail, stat tiles, task list, task cards, status badges, solid primary action, secondary outline actions, and responsive single-column layout at 900px and below. Use visible keyboard focus styles and `prefers-reduced-motion` handling.

- [ ] **Step 4: Add task statistics and visual task hierarchy**

In `app/static/app.js`, add `renderTaskStats(tasks)` and call it from `renderTasks(tasks)`. Count `running`, `done`, and recoverable interrupted tasks from the given list; render only using `textContent`. Keep existing fetch calls, ids, button class names, and action listeners. Render status and task actions as grouped visual controls without changing their existing endpoint behavior.

```javascript
function renderTaskStats(tasks) {
  const stats = {
    running: tasks.filter(task => task.status === "running").length,
    done: tasks.filter(task => task.status === "done").reduce((sum, task) => sum + task.items_count, 0),
    attention: tasks.filter(task => task.status === "error" && task.pending_cards).length,
  };
  // Render three stat tiles into #taskStats with textContent.
}
```

- [ ] **Step 5: Run the contract test to verify it passes**

Run: `venv\Scripts\python.exe -m unittest tests.test_frontend_contract -v`

Expected: PASS with both workspace and compatibility checks green.

- [ ] **Step 6: No commit**

This project is not a Git repository. Continue to visual verification.

### Task 3: Verify the visual and packaged result

**Files:**
- Modify: none unless verification exposes a layout defect.
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: the rebuilt static workspace and current FastAPI app.
- Produces: verified source UI and refreshed distributable ZIP archives.

- [ ] **Step 1: Run source checks**

Run: `venv\Scripts\python.exe -m unittest tests.test_frontend_contract -v` and `venv\Scripts\python.exe -m compileall app desktop.py`.

Expected: frontend contract passes and Python compilation exits with code 0.

- [ ] **Step 2: Start the source server and inspect desktop layout**

Run: `venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`.

Inspect: the 1200px-wide dashboard has no overlap, the side rail remains readable, task controls are visible, and the logo renders.

- [ ] **Step 3: Inspect narrow layout**

Inspect at a 390px viewport: the rail stacks above tasks, links wrap, buttons remain clickable, and no text or control overlaps.

- [ ] **Step 4: Rebuild distributables**

Run: `venv\Scripts\python.exe -m PyInstaller desktop.spec --noconfirm` after closing any running `AvitoParser.exe`. Recreate `dist/AvitoParser.zip` with only the new executable and `dist/AvitoParser_с_ключами.zip` with the new executable plus existing `data/captcha_key.txt` and `data/spfa_key.txt`.

- [ ] **Step 5: Verify archives**

Run: `tar -tf dist/AvitoParser.zip` and `tar -tf dist/AvitoParser_с_ключами.zip`.

Expected: the ordinary archive contains only `AvitoParser.exe`; the keyed archive contains `AvitoParser.exe`, `data/captcha_key.txt`, and `data/spfa_key.txt`.

- [ ] **Step 6: No commit**

This project is not a Git repository. Report the verified archive locations to the user.
