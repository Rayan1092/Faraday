# 2026-11-15 08:15 Dell: no promises, reasons that fit the label (Mac retest 3)

From the owner's third Mac retest (07:40 log):

- **Visitor replies never claim abilities.** Bonsai told someone at the intercom "I will
  check the app settings for you". `brain/guard.claims_ability()` spots a line that
  promises something Faraday can't do: "I'll / I can / let me / we will" followed by
  anything except passing things on (let the owner know, tell, pass, take or save a
  message, help with a delivery), plus "I'm calling / checking / opening ...". `Brain.decide`
  drops such a speak (recorded in `dropped`). If nothing is left, the fallback answers
  (armed or intercom: the message is saved and "Thank you. I'll pass that along."). Both
  visitor prompts also say to offer only a message. The small prompt stays under 400
  characters.
- **Label reasons agree with the label.** "visitor" with "doorbell while away, no one
  visible" got through because the doorbell part was true. `threat.agrees()` rejects
  a person label whose reason says no one is there, and "nobody" with a person in the
  reason. Code then writes the reason from the facts (`reason_for`).
- Tests: `brain/tests/test_no_promises.py`.
- **Dashboard (owner, 08:00):** the main dashboard has no light switches anymore, only a
  read-only on/off status for each lamp (`renderLights` in `dashboard/static/dashboard.js`).
  Switching lights is on the Controls page only. `POST /api/control` stays, because
  controls.js uses it. The video script now switches the porch light off on the Controls
  page.

Not in this PR: instruction phrasing and cancel-and-replace (retest items 1 and 2). The
owner asked for one general standing-task tool (remember / update_task / remove_task /
list_tasks) to replace instructions, rules and reminders, and that tool covers both.
It's a separate, larger change after the 8:30 freeze.
