"""Run from the EVA folder:  python -m unittest tests.test_understanding -v"""
import unittest

from eva.common.types import Mode
from eva.understanding.request import InputSource
from eva.understanding.understand import understand


class UnderstandTests(unittest.TestCase):
    def test_empty_input_returns_none(self):
        self.assertIsNone(understand("", InputSource.TEXT))
        self.assertIsNone(understand("   \n\t ", InputSource.TEXT))

    def test_only_wake_phrase_returns_none(self):
        self.assertIsNone(understand("Hey EVA", InputSource.VOICE))
        self.assertIsNone(understand("Hey! Eva,", InputSource.VOICE))

    def test_hey_eva_makes_a_command(self):
        for text in ("Hey EVA open notepad", "hey eva, open notepad",
                     "Hey! Eva open notepad", "HEY EVA: open notepad"):
            r = understand(text, InputSource.VOICE)
            self.assertEqual(r.mode, Mode.COMMAND_MODE, text)
            self.assertEqual(r.clean_text, "open notepad", text)

    def test_raw_text_is_kept(self):
        r = understand("Hey EVA, open notepad", InputSource.VOICE)
        self.assertEqual(r.raw_text, "Hey EVA, open notepad")

    def test_normal_sentence_is_conversation(self):
        for text in ("open notepad", "tell me a joke", "how are you",
                     "what time is it", "eva open notepad"):
            r = understand(text, InputSource.TEXT)
            self.assertEqual(r.mode, Mode.CONVERSATION_MODE, text)
            self.assertEqual(r.clean_text, text, text)

    def test_wake_phrase_in_the_middle_is_ignored(self):
        r = understand("I said hey eva to my sister", InputSource.TEXT)
        self.assertEqual(r.mode, Mode.CONVERSATION_MODE)

    def test_similar_words_are_not_the_wake_phrase(self):
        for text in ("Hey evaluate this", "Heyeva open notepad"):
            r = understand(text, InputSource.TEXT)
            self.assertEqual(r.mode, Mode.CONVERSATION_MODE, text)

    def test_spaces_are_fixed(self):
        r = understand("  hey   eva   open    chrome \n", InputSource.TEXT)
        self.assertEqual(r.clean_text, "open chrome")

    def test_source_and_confidence_kept(self):
        r = understand("Hey EVA open notepad", InputSource.VOICE, confidence=0.91)
        self.assertEqual(r.source, InputSource.VOICE)
        self.assertEqual(r.confidence, 0.91)

    def test_each_request_gets_its_own_id(self):
        a = understand("open notepad", InputSource.TEXT)
        b = understand("open notepad", InputSource.TEXT)
        self.assertNotEqual(a.request_id, b.request_id)


if __name__ == "__main__":
    unittest.main()
