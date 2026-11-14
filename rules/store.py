"""User rules from add_rule ("when there's motion at night, turn on the lamp too").

Rules are kept in a JSON file (default logs/rules.json, gitignored runtime
data) so they survive a restart. Every rule is checked with the same rules as
an add_rule action from the brain, on add, on load and again when it fires:
- shared validation (known event, known modes, 1-3 valid steps, no add_rule),
- no set_mode at all (PLAN.md Q19: rules can't change mode),
- only doorbell / motion / speech / photo events can trigger a rule,
- a photo can't trigger a new photo (that would loop).
A rule that fails any check is never stored or fired.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from shared import Action, ActionType, Event, EventType, Source, make_action, new_id, now_iso
from shared.actions import validate_args

REPO_ROOT = Path(__file__).resolve().parents[1]
TRIGGERS = frozenset({EventType.DOORBELL, EventType.MOTION,
                      EventType.SPEECH_HEARD, EventType.PHOTO_DESCRIBED})

Rule = Dict[str, Any]


def check_rule(args: Any) -> List[str]:
    """Problems with an add_rule args dict; empty means it may be stored and fired."""
    if not isinstance(args, dict):
        return ["rule must be an object"]
    errors = validate_args(ActionType.ADD_RULE, args)
    if errors:
        return errors
    when, then = args["when"], args["then"]
    if when.get("event") not in TRIGGERS:
        errors.append(f"rules can't trigger on {when.get('event')}")
    for step in then:
        if step["type"] == ActionType.SET_MODE:
            errors.append("rules may not change mode")
        if step["type"] == ActionType.TAKE_PHOTO and when["event"] == EventType.PHOTO_DESCRIBED:
            errors.append("a photo can't trigger another photo")
    return errors


class RuleStore:
    def __init__(self, path: Union[str, Path]):
        path = Path(path)
        self.path = path if path.is_absolute() else REPO_ROOT / path
        self._lock = threading.Lock()
        self._rules: List[Rule] = []
        self.skipped: List[str] = []  # problems found while loading, for the console
        self._load()

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "RuleStore":
        logs_dir = settings.get("paths", {}).get("logs", "logs")
        return cls(Path(logs_dir) / "rules.json")

    # ---- public ------------------------------------------------------------

    def add(self, args: Dict[str, Any], description: str = "",
            caused_by: Optional[str] = None) -> Optional[Rule]:
        """Store a rule. Returns it, the existing identical rule, or None if refused."""
        if check_rule(args):
            return None
        when = {"event": args["when"]["event"], "modes": sorted(set(args["when"].get("modes", [])))}
        then = [{"type": s["type"], "args": dict(s["args"])} for s in args["then"]]
        with self._lock:
            for rule in self._rules:
                if rule["when"] == when and rule["then"] == then:
                    return dict(rule)
            rule = {"id": new_id("rule"), "when": when, "then": then,
                    "description": (args.get("description") or description).strip()[:200],
                    "created": now_iso(), "caused_by": caused_by}
            self._rules.append(rule)
            self._save()
            return dict(rule)

    def remove(self, rule_id: str) -> bool:
        with self._lock:
            before = len(self._rules)
            self._rules = [r for r in self._rules if r["id"] != rule_id]
            if len(self._rules) != before:
                self._save()
                return True
        return False

    def all(self) -> List[Rule]:
        with self._lock:
            return json.loads(json.dumps(self._rules))

    def fire(self, event: Event, mode: str) -> List[Action]:
        """Actions from every rule that matches this event in this mode (source rules)."""
        actions: List[Action] = []
        for rule in self.all():
            when = rule["when"]
            if when["event"] != event.type or (when["modes"] and mode not in when["modes"]):
                continue
            if check_rule({"when": when, "then": rule["then"]}):
                continue  # file edited by hand into something unsafe
            for step in rule["then"]:
                try:
                    actions.append(make_action(step["type"], step["args"], Source.RULES,
                                               caused_by=event.id))
                except ValueError:
                    continue
        return actions

    # ---- file --------------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            self.skipped.append(f"could not read {self.path.name}: {e}")
            return
        for rule in data.get("rules", []) if isinstance(data, dict) else []:
            problems = check_rule({"when": rule.get("when"), "then": rule.get("then")}
                                  if isinstance(rule, dict) else None)
            if problems or not isinstance(rule.get("id"), str):
                self.skipped.append(f"skipped rule {rule!r:.80}: {'; '.join(problems) or 'no id'}")
                continue
            rule["when"].setdefault("modes", [])
            self._rules.append(rule)

    def _save(self) -> None:
        """Write to a temp file and swap it in, so a crash never leaves half a file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"rules": self._rules}, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, self.path)
