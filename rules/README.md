# rules/

**Owner: Dell laptop.**

User rules created by `add_rule` ("when motion at night, turn on the porch light"). It matches incoming events against the stored rules and emits their actions with `source: "rules"`. A rule can never add rules or disarm.

| File | What it does |
|---|---|
| `baseline.py` | The mode table from [docs/PLAN.md](../docs/PLAN.md#mode-table-baseline-plain-code) in plain code. `baseline_actions(event, mode, alert)` gives the guaranteed lights, photos and fixed warning lines, escalating with the alert level. `merge(existing, extra)` drops actions that repeat or contradict ones already emitted: one photo per event, and no `outlet_off` for an outlet just turned on |
| `instructions.py` | `InstructionStore` and the plain-code parser for **owner instructions** (see below). Kept in `<paths.logs>/instructions.json` |
| `store.py` | `RuleStore`: rules by voice. Kept in `<paths.logs>/rules.json` (gitignored runtime data), so they survive a restart. `fire(event, mode)` gives the matching rules' actions |

**Guard rails for user rules.** These run on add, on load (in case the file was edited by hand) and again when a rule fires, via `check_rule()`:
- shared validation,
- no `set_mode` at all,
- no `add_rule`,
- trigger events limited to doorbell, motion, speech and photo,
- a photo can't trigger a photo.

**When rules can be added.** In Home or Night only. In Away the speaker is a visitor, so the brain isn't offered `add_rule`.

## Owner instructions (words for visitors)

"Hey Faraday, if a delivery comes, ask them to leave it by the side door." These are standing instructions about **what Faraday says**, not what it does.
- **Parsing:** `parse_instruction()` (plain code, no AI) finds "if/when <who> comes, ask/tell/let them know ..." and "ask/tell <who> to ...". The audience is a delivery, a visitor or anyone. There's one instruction per audience: a new one replaces the old.
- **Owner only:** the pipeline accepts them at **Home** only, because anything said while armed could be a visitor. Faraday confirms out loud. "Forget the delivery instruction" or "clear all my instructions" removes them by voice; the dashboard's Rules & instructions card removes them by tap.
- **Words only:** a request that mentions lights, lamps, outlets, switching, modes, the alarm, locks, opening doors, letting someone in, codes or passwords is refused (also on load, if the file was edited by hand). The text is never turned into an action.
- **Where they're used:** `Brain.instructions_for()` picks the relevant ones by threat label (delivery → delivery + anyone; visitor or no label → visitor + anyone; none for suspicious or nobody). They go into the visitor prompt for doorbell and delivery photos and for a visitor talking while armed. Plain code then makes sure the first greeting passes them on. A delivery hears the owner's spot instead of the default "front door" line (a conflicting drop-off line from Bonsai is replaced). Without Bonsai, the fallback greeting says them too, in the third person ("The owner is in the back garden.").

The demo line "When there's motion at night, turn on the living room lamp too." becomes `{"when": {"event": "motion", "modes": ["night"]}, "then": [{"type": "outlet_on", "args": {"outlet": "living_room_lamp"}}]}`. Bonsai or the plain-code parser in `brain/fallback.py` produces it.
