# rules/

**Owner: Dell laptop.**

User rules created by `add_rule` ("when motion at night, turn on the porch light"). It matches incoming events against the stored rules and emits their actions with `source: "rules"`. A rule can never add rules or disarm.

**Built so far: `baseline.py`.** This is the mode table from [docs/PLAN.md](../docs/PLAN.md#mode-table-baseline-plain-code) in plain code. `baseline_actions(event, mode)` gives the guaranteed lights and photos for doorbell and motion. `merge(existing, extra)` drops brain actions that repeat or contradict them: one photo per event, and no `outlet_off` for an outlet the baseline just turned on.

Not built yet: storing and firing user rules from `add_rule`.
