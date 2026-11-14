# brain/

**Owner: Dell laptop.**

Decides what to do. `handle_event(event) -> list[Action]` checks `modes/` and `rules/`, then asks Bonsai 2 (LM Studio, OpenAI-compatible) with a **short** prompt, and validates the reply with `shared.validate_action()`. Also contains `describe_photo()` (Bonsai vision → `photo_described`) and the `summarize_log` handler.

Bonsai's reply format is described in [docs/FORMATS.md](../docs/FORMATS.md#what-the-llm-returns). The brain can never disarm.
