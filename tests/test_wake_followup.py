import unittest

from eva.common.types import Mode
from eva.understanding.request import InputSource
from eva.understanding.understand import is_wake_only, understand


class IsWakeOnlyTests(unittest.TestCase):
    def test_wake_word_alone(self):
        for t in ["eva", "Eva.", "EVA!", "hey eva", "Hey, Eva!", "ok eva", "  ava  ", "Hello Eva"]:
            self.assertTrue(is_wake_only(t), t)

    def test_not_wake_only(self):
        for t in ["eva open notepad", "hey eva what time is it", "open notepad", "evaluate",
                  "Eva's", "Evan", "", "   ", None, "hello there"]:
            self.assertFalse(is_wake_only(t), t)


class ForceCommandTests(unittest.TestCase):
    def run_(self, text, force):
        return understand(text, InputSource.VOICE, 0.9, force_command=force)

    def test_next_sentence_is_a_command_without_wake_word(self):
        r = self.run_("open notepad", True)
        self.assertEqual((r.mode, r.clean_text), (Mode.COMMAND_MODE, "open notepad"))

    def test_wake_word_inside_the_follow_up_is_still_removed(self):
        r = self.run_("Eva open notepad", True)
        self.assertEqual((r.mode, r.clean_text), (Mode.COMMAND_MODE, "open notepad"))

    def test_nothing_to_do_cases(self):
        for t in ["eva", "", "   "]:
            self.assertIsNone(self.run_(t, True), t)

    def test_default_is_unchanged(self):
        r = self.run_("open notepad", False)
        self.assertEqual(r.mode, Mode.CONVERSATION_MODE)
        self.assertEqual(understand("open notepad", InputSource.VOICE).mode, Mode.CONVERSATION_MODE)
        self.assertEqual(self.run_("Eva open notepad", False).mode, Mode.COMMAND_MODE)


if __name__ == "__main__":
    unittest.main()
