# 2026-11-15 08:30 Dell: one standing-task tool (owner request, after the freeze)

The owner asked (07:55): "Make it general, not more presets: replace separate instructions,
rules and reminders with ONE standing-task tool that Bonsai uses."

## What it is

- **`rules/tasks.py`**: the task store (logs/tasks.json) and the plain-code checks.
  - **Trigger:** home (passphrase disarm), doorbell, delivery, motion, mode_change, time
    (HH:MM) or owner_speaks. `night` limits a task to Night mode or 21:00–06:00.
  - **Action:** say_owner ("Reminder: …"), say_visitor, lights_on/off with one or more outlets,
    set_mode (away or night only, never home, and only from Home), or take_photo.
  - **Repeat:** once or always.
  - The words for describing and confirming a task live here too: `describe`, `confirmation`,
    `updated_line`, `removed_line` and `list_line`.
  - `relevant(label)` hands say_visitor tasks to the visitor conversation as owner instructions,
    so the delivery reply and the visitor prompt work unchanged.
  - `fire()` runs house actions and reminders on events. `due()` runs clock tasks;
    `on_owner_speech()` runs "next time I talk to you" tasks.
- **`brain/tasks.py`**: the smart Bonsai (the 9B on the Mac) calls one of four tools: `remember`,
  `update_task`, `remove_task` or `list_tasks`. Code then checks the call against the owner's words:
  - The trigger, "at night", the time, the lights and the mode must all be in the words. The
    clock time always comes from the words.
  - Every content word of what will be said must be the owner's. The one exception is a
    leading request verb, as with "leave" for "put" in the Mac retest.
  - "Make that the side door" puts the new words in place of the matching old ones.
  - If Bonsai answers a correction with `remember`, it is treated as an update.
- **Router** (`brain/owner.py`): in `tasks_mode`, add_rule, add_instruction and the new `task`
  intent all become `task`. Strong task words come before arming, so "at 9 PM switch to night
  mode" never arms now. Out-of-scope requests get a `topic` that code checks, and
  `lines.cant_do(topic)` builds the reply: "Sorry, I can't check the weather. I work offline, …".
- **Pipeline:**
  - `_owner_task` stores the task and says back in one sentence what it understood.
  - Tasks run after Faraday's own reply, so "Welcome home" comes first and then the reminder.
  - A clock timer handles time tasks.
  - With no model, the keywords' instruction or rule is stored as the task.
- **Dashboard:** the card is called Tasks. It shows each task as Faraday confirmed it, with
  what the owner said and a delete button.
- **Bench:** 17 `k_` cases, run with `--only k_…`.
- Tests: `brain/tests/test_tasks_tool.py` and the dashboard `TasksCard`.

## Still to do and know

- The older `RuleStore` and `InstructionStore` stay for integration's `--log` mode and the old
  tests. New owner requests never go to them.
- The CLAUDE.md AI tool list says the actions a task runs are the same eight. The four task tools
  only store, change, remove or list tasks. NV: please confirm.
