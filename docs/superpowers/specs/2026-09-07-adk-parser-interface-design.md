# ADK Parser Interface Redesign

## Goal

Replace the current light two-column form with a dense, dark operational workspace
inspired by the public ADK RUS visual language. Keep every existing parser action,
API request, and task state unchanged.

## Visual Direction

- Use an industrial dark palette: near-black background, layered charcoal surfaces,
  white text, and orange as the single primary action accent.
- Use the ADK RUS logo in the top application bar, as authorized by the user.
- Use the extracted typography direction: Play for interface copy and Ubuntu for
  display labels where available, with dependable system fallbacks.
- Keep radii restrained, use a five-pixel spacing rhythm, and avoid decorative
  gradients or floating-card composition.

## Screen Structure

1. A compact top bar contains the logo, product name, and the current workspace label.
2. A persistent left control rail contains the new-task form, link input, collection
   limit, output options, and collapsed advanced settings.
3. The main area starts with operational statistics and a prominent new-task action.
4. The task list is the primary work surface. It distinguishes active, completed,
   blocked, and browser-closed tasks through status color, labels, progress bars,
   and appropriately grouped actions.

## Interaction Rules

- Existing element ids and API calls remain intact so task creation, key settings,
  proxy settings, downloads, cancellation, repeat, captcha continuation, and
  unfinished-task recovery keep working.
- Links and task actions must remain keyboard accessible and clear at narrow widths.
- The unfinished-task action is shown only after a stopped task has saved work.
- Partial files remain downloadable for recoverable interrupted tasks.

## Files in Scope

- `app/static/index.html`: semantic page structure and visual grouping only.
- `app/static/style.css`: complete responsive ADK-inspired design layer.
- `app/static/app.js`: presentation-only task statistics and task status structure;
  existing network behavior is preserved.
- `app/static/`: copied ADK RUS logo asset, with an attribution-free local reference.

## Verification

- Static checks confirm the existing JavaScript element ids and API endpoint strings
  remain present.
- The app imports successfully after the frontend changes.
- Desktop and narrow responsive screenshots are inspected for layout overlap, button
  visibility, task action grouping, and readable status contrast.
