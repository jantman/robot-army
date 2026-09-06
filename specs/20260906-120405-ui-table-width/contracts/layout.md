# Contract: page width

The web interface's layout is decided entirely by `APP_CSS` in `src/robot_army/web/html.py`. This is
what that stylesheet must say about width, and what a test may hold it to. It is a contract with the
next person to edit the stylesheet, not with any outside consumer.

## What the stylesheet must state

1. **Two widths exist and differ.** A prose measure and a page bound, as custom properties on `:root`,
   with the page bound the larger of the two. One value used for both would be the bug this feature
   fixes, wearing two names.

2. **The content area is bounded by the page value.** `main` carries `max-width: var(--page)` and stays
   centred. It must not carry the measure.

3. **Text-shaped elements are bounded by the measure.** Paragraphs, lists, definition lists, banners,
   cards, audit records and filter rows inside `main` carry `max-width: var(--measure)`. The rule is
   written as a descendant selector so it holds at any nesting depth.

4. **The table container is shrink-to-fit.** `.scroll` carries `width: fit-content` and
   `max-width: 100%`, so a table takes the width its content needs, up to the space available, and is
   never stretched to fill space it does not need.

5. **The table container still scrolls.** `.scroll` keeps `overflow-x: auto`. This is the load-bearing
   half: without it, capping the container at `100%` on a phone would compress an eight-column table
   into 358 pixels instead of letting it scroll.

6. **Tables still fill their container.** `table { width: 100% }` survives. Combined with (4) the
   container sizes to the content and the table fills that; combined with (5) a table whose content
   exceeds the container grows past it and scrolls.

## What must remain true of the markup

7. **Every table is inside a `div.scroll`.** `html.table()` is the only thing that builds a `<table>`,
   and it wraps one. A page that hand-rolled a table would escape every rule above, so this is asserted
   against rendered views rather than assumed.

## What this contract does not fix

- The values themselves. `60rem` and `120rem` are judgements, and a later change to either is a change
  to taste, not a breach.
- Which elements count as prose. The selector list will grow as views gain new kinds of text; adding to
  it is expected.
- Anything about colour, spacing, or type. Untouched by this feature.

## What a breach looks like

- `main` bounded by the measure again: every table back to half a window.
- `.scroll` without `overflow-x: auto`: an eight-column table unreadable and unscrollable on a phone.
- `.scroll` full-width instead of shrink-to-fit: a two-column table stretched across a 1900-pixel
  window.
- A table built outside `html.table()`: one table in the interface obeying none of this, silently.
