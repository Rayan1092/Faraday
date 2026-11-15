# Dashboard polish re-check (Dell, item 6)
- Laptop / branch / PR: Dell / `dell/polish-check` / PR "polish re-check"
- What changed: re-checked the PR #22 polish at 1280×720 and 1920×1080 (Home after a summary, doorbell toast, warning toast, alarm), now with the new Controls button in the dock. Everything held except one thing: at 1280×720 the last message's sender and time sat inside the Messages card's bottom fade, half cut. Messages now follow the Heard & Said rule: whole entries only, dropping the oldest until the rest fit. If the newest alone doesn't fit, its text is clamped to two lines so the sender and time stay visible. The fade is gone from Messages (kept on Rules). Refit on resize.
- Checked and fine: toasts top right with the timeline pushed below, whole Heard & Said entries, one merged entry per photo ("Photo · reason · Delivery"), compact Messages card, soft fade on the timeline, dock fits with the Controls button at both sizes.
- How it was tested: `python -m unittest discover` passes. New `dashboard/tests/test_polish.py` keeps the rules in the page code. Screenshots at both sizes.
- Next: item 7 (Listen button).
