"""Polish rules that live in the page code (checked at 1280x720 and 1920x1080
with screenshots; these tests keep the rules from silently disappearing)."""
import re
import unittest

from dashboard.server import STATIC_DIR

JS = (STATIC_DIR / "dashboard.js").read_text(encoding="utf-8")
CSS = (STATIC_DIR / "dashboard.css").read_text(encoding="utf-8")


class WholeEntries(unittest.TestCase):
    def test_heard_and_said_and_messages_drop_whole_entries(self):
        # both lists trim until the rest fit, instead of cutting an entry in half
        self.assertGreaterEqual(len(re.findall(r"scrollHeight > list\.clientHeight \+ 1", JS)), 3)
        self.assertIn('classList.add("clamp")', JS)       # one long message: clamp the text, keep its meta

    def test_messages_are_not_faded_out(self):
        self.assertNotRegex(CSS, r"\.messages[^{]*\{[^}]*mask-image")
        self.assertIn(".messages li.clamp .msg-text", CSS)

    def test_lists_refit_on_resize(self):
        self.assertIn('addEventListener("resize", () => renderTalk', JS)
        self.assertIn('addEventListener("resize", () => renderNotes', JS)


class Toasts(unittest.TestCase):
    def test_toast_sits_top_right_and_pushes_the_timeline(self):
        self.assertIn("body.toast-on .timeline-card", CSS)


if __name__ == "__main__":
    unittest.main()
