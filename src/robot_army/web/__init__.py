"""The web interface: a second front end onto milestone 001's operations layer.

Three modules, and the split is the design (plan.md, R2):

* :mod:`robot_army.web.html` — escaping, element helpers, the page chrome, and the
  embedded CSS and JavaScript. Nothing here knows what a work item is.
* :mod:`robot_army.web.pages` — one function per view, each returning a plain payload
  dict that is simultaneously the JSON representation and the input to the renderer.
* :mod:`robot_army.web.server` — the route table, request dispatch, action handling,
  and the startup preconditions.

The rule that keeps the two front ends from diverging (FR-047): **every action goes
through** :mod:`robot_army.operations`. If a route needs logic that is not there, the
logic goes there, not here.
"""

from __future__ import annotations
