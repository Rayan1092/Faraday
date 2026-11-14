"""Passphrase matching survives STT quirks: digits, punctuation, case, hyphens."""
import unittest

from modes.passphrase import canonical_words, find_passphrase, number_words, redact


def matches(text, secret):
    return find_passphrase(text, secret) is not None


class Matching(unittest.TestCase):
    def test_punctuation_case_and_spacing(self):
        for text in ("silver otter lantern", "Silver, otter... LANTERN!", "silver-otter lantern",
                     "Okay Faraday, silver otter lantern, please.", "  SILVER\totter\nlantern  "):
            self.assertTrue(matches(text, "silver otter lantern"), text)

    def test_wrong_or_partial_never_matches(self):
        for text in ("silver otter", "silver lantern otter", "silverotter lantern",
                     "silver otters lantern", "sliver otter lantern", "", "[passphrase]"):
            self.assertFalse(matches(text, "silver otter lantern"), text)

    def test_stt_writes_numbers_as_digits(self):
        cases = [("blue pelican seventeen", "Blue pelican 17."),
                 ("forty two ravens", "42 ravens"),
                 ("forty two ravens", "Forty-two, ravens!"),
                 ("one hundred and five geese", "105 geese"),
                 ("one hundred and five geese", "one hundred five geese"),
                 ("seven bridges", "7 bridges"),
                 ("7 bridges", "seven bridges"),
                 ("twelve hundred stars", "twelve hundred stars"),
                 ("one thousand two hundred fifty stars", "1,250 stars"),
                 ("agent zero zero seven", "Agent 007")]
        for secret, heard in cases:
            self.assertTrue(matches(heard, secret), (secret, heard))

    def test_numbers_must_still_be_the_right_numbers(self):
        for heard in ("blue pelican 71", "blue pelican 7", "blue pelican 170", "blue pelican seven"):
            self.assertFalse(matches(heard, "blue pelican seventeen"), heard)

    def test_apostrophes(self):
        for heard in ("Don’t panic", "don't panic", "dont panic"):
            self.assertTrue(matches(heard, "don't panic"), heard)

    def test_empty_passphrase_never_matches(self):
        for secret in ("", "   ", "!!!", "--"):
            self.assertFalse(matches("anything at all", secret), repr(secret))
            self.assertEqual(redact("anything", secret), ("anything", False))

    def test_number_words(self):
        self.assertEqual(number_words("17"), ["seventeen"])
        self.assertEqual(number_words("90"), ["ninety"])
        self.assertEqual(number_words("105"), ["one", "hundred", "five"])
        self.assertEqual(number_words("2000"), ["two", "thousand"])
        self.assertEqual(number_words("07"), ["zero", "seven"])
        self.assertEqual(len(number_words("12345678")), 8)  # long numbers digit by digit
        self.assertEqual([w for w, _, _ in canonical_words("A-1!")], ["a", "one"])


class Redaction(unittest.TestCase):
    def test_redacts_with_original_spelling(self):
        text, hit = redact("Hi. Blue, PELICAN 17 - disarm", "blue pelican seventeen")
        self.assertTrue(hit)
        self.assertEqual(text, "Hi. [passphrase] - disarm")

    def test_every_occurrence(self):
        text, _ = redact("silver otter lantern and again silver otter lantern", "silver otter lantern")
        self.assertEqual(text, "[passphrase] and again [passphrase]")

    def test_no_match_leaves_text_alone(self):
        self.assertEqual(redact("Package for Sam.", "silver otter lantern"),
                         ("Package for Sam.", False))

    def test_odd_passphrases_terminate(self):
        self.assertEqual(redact("say passphrase now", "passphrase")[0], "say [passphrase] now")
        self.assertEqual(redact("100100", "hundred")[0], "[passphrase]")


if __name__ == "__main__":
    unittest.main()
