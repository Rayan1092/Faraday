# rules/

**Owner: Dell laptop.**

User rules created by `add_rule` ("when motion at night, turn on the porch light"). It matches incoming events against the stored rules and emits their actions with `source: "rules"`. A rule can never add rules or disarm.

| File | What it does |
|---|---|
| `baseline.py` | The mode table from [docs/PLAN.md](../docs/PLAN.md#mode-table-baseline-plain-code) in plain code. `baseline_actions(event, mode, alert)` gives the guaranteed lights, photos and fixed warning lines, escalating with the alert level. `merge(existing, extra)` drops actions that repeat or contradict ones already emitted: one photo per event, and no `outlet_off` for an outlet just turned on |
| `store.py` | `RuleStore`: rules by voice. Kept in `<paths.logs>/rules.json` (gitignored runtime data), so they survive a restart. `fire(event, mode)` gives the matching rules' actions |

**Guard rails for user rules.** These run on add, on load (in case the file was edited by hand) and again when a rule fires, via `check_rule()`:
- shared validation,
- no `set_mode` at all,
- no `add_rule`,
- trigger events limited to doorbell, motion, speech and photo,
- a photo can't trigger a photo.

**When rules can be added.** In Home or Night only. In Away the speaker is a visitor, so the brain isn't offered `add_rule`.

The demo line "When there's motion at night, turn on the living room lamp too." becomes `{"when": {"event": "motion", "modes": ["night"]}, "then": [{"type": "outlet_on", "args": {"outlet": "living_room_lamp"}}]}`. Bonsai or the plain-code parser in `brain/fallback.py` produces it.
