import sys
import time
import types
import unittest

import numpy as np

from eva.input.speech_to_text import (
    GoogleEngine, SpeechToText, SttError, Transcript,
    audio_to_pcm16, parse_google_result,
)
from eva.input.voice_settings import VoiceSettings


class FakeEngine:
    def __init__(self, name, result=None, error=None):
        self.name, self.result, self.error, self.calls = name, result, error, 0

    def transcribe(self, audio):
        self.calls += 1
        if self.error:
            raise SttError(self.error)
        return self.result

    def load(self):
        pass


def fake_sr(result=None, raises=None):
    """A fake 'speech_recognition' module."""
    m = types.SimpleNamespace()

    class UnknownValueError(Exception): ...
    class RequestError(Exception): ...

    class AudioData:
        def __init__(self, data, rate, width):
            self.data, self.rate, self.width = data, rate, width

    class Recognizer:
        operation_timeout = None
        last_kwargs = {}

        def recognize_google(self, data, **kw):
            Recognizer.last_kwargs = kw
            if raises == "unknown":
                raise UnknownValueError()
            if raises == "request":
                raise RequestError("no internet")
            if raises == "other":
                raise RuntimeError("weird")
            return result

    m.UnknownValueError, m.RequestError = UnknownValueError, RequestError
    m.AudioData, m.Recognizer = AudioData, Recognizer
    return m


class HelperTests(unittest.TestCase):
    def test_quiet_audio_is_made_louder_but_capped(self):
        quiet = np.full(1000, 0.01, dtype=np.float32)
        pcm = np.frombuffer(audio_to_pcm16(quiet), dtype="<i2")
        self.assertAlmostEqual(abs(int(pcm[0])) / 32767, 0.08, places=2)  # 8x cap

    def test_loud_audio_not_changed_and_no_clipping(self):
        loud = np.array([0.9, -0.95, 2.0], dtype=np.float32)
        pcm = np.frombuffer(audio_to_pcm16(loud), dtype="<i2")
        self.assertEqual(int(pcm[2]), 32767)
        self.assertEqual(len(pcm), 3)

    def test_normalize_off(self):
        quiet = np.full(10, 0.01, dtype=np.float32)
        pcm = np.frombuffer(audio_to_pcm16(quiet, normalize=False), dtype="<i2")
        self.assertAlmostEqual(int(pcm[0]) / 32767, 0.01, places=3)

    def test_silence_stays_silence(self):
        pcm = np.frombuffer(audio_to_pcm16(np.zeros(10, dtype=np.float32)), dtype="<i2")
        self.assertTrue((pcm == 0).all())

    def test_parse_result(self):
        r = {"alternative": [{"transcript": " open notepad ", "confidence": 0.93},
                             {"transcript": "open note pad"}], "final": True}
        self.assertEqual(parse_google_result(r), Transcript("open notepad", 0.93))

    def test_parse_no_confidence_and_empty(self):
        self.assertEqual(parse_google_result({"alternative": [{"transcript": "hi"}]}),
                         Transcript("hi", None))
        self.assertEqual(parse_google_result([]), Transcript("", None))
        self.assertEqual(parse_google_result(None), Transcript("", None))


class GoogleEngineTests(unittest.TestCase):
    audio = np.full(1600, 0.2, dtype=np.float32)

    def test_success_uses_indian_english_and_timeout(self):
        sr = fake_sr({"alternative": [{"transcript": "hello eva", "confidence": 0.9}]})
        t = GoogleEngine(VoiceSettings(), sr).transcribe(self.audio)
        self.assertEqual(t.text, "hello eva")
        self.assertEqual(sr.Recognizer.last_kwargs["language"], "en-IN")

    def test_not_understood_is_empty_not_error(self):
        t = GoogleEngine(VoiceSettings(), fake_sr(raises="unknown")).transcribe(self.audio)
        self.assertEqual(t.text, "")

    def test_offline_raises_stt_error(self):
        with self.assertRaises(SttError) as cm:
            GoogleEngine(VoiceSettings(), fake_sr(raises="request")).transcribe(self.audio)
        self.assertIn("could not be reached", str(cm.exception))

    def test_other_exception_becomes_stt_error(self):
        with self.assertRaises(SttError):
            GoogleEngine(VoiceSettings(), fake_sr(raises="other")).transcribe(self.audio)

    def test_missing_package_message(self):
        sys.modules.pop("speech_recognition", None)
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def blocked(name, *a, **k):
            if name == "speech_recognition":
                raise ImportError("nope")
            return real_import(name, *a, **k)

        import builtins
        builtins.__import__ = blocked
        try:
            with self.assertRaises(SttError) as cm:
                GoogleEngine(VoiceSettings()).transcribe(self.audio)
        finally:
            builtins.__import__ = real_import
        self.assertIn("pip install SpeechRecognition", str(cm.exception))


class SpeechToTextTests(unittest.TestCase):
    audio = np.zeros(10, dtype=np.float32)

    def make(self, google, whisper, **settings):
        return SpeechToText(VoiceSettings(**settings), google=google, whisper=whisper)

    def test_google_is_main(self):
        g = FakeEngine("google", Transcript("from google", 0.9))
        w = FakeEngine("whisper", Transcript("from whisper", 0.5))
        stt = self.make(g, w)
        self.assertEqual(stt.transcribe(self.audio).text, "from google")
        self.assertEqual((g.calls, w.calls, stt.last_engine), (1, 0, "google"))

    def test_falls_back_to_whisper_when_google_fails(self):
        g = FakeEngine("google", error="offline")
        w = FakeEngine("whisper", Transcript("from whisper", 0.5))
        stt = self.make(g, w)
        self.assertEqual(stt.transcribe(self.audio).text, "from whisper")
        self.assertEqual(stt.last_engine, "whisper")

    def test_google_skipped_after_failure(self):
        g = FakeEngine("google", error="offline")
        w = FakeEngine("whisper", Transcript("w", 0.5))
        stt = self.make(g, w)
        stt.transcribe(self.audio)
        stt.transcribe(self.audio)
        self.assertEqual(g.calls, 1)
        self.assertEqual(w.calls, 2)

    def test_google_tried_again_after_wait(self):
        g = FakeEngine("google", error="offline")
        w = FakeEngine("whisper", Transcript("w", 0.5))
        stt = self.make(g, w, retry_google_after_s=0.05)
        stt.transcribe(self.audio)
        time.sleep(0.1)
        stt.transcribe(self.audio)
        self.assertEqual(g.calls, 2)

    def test_not_understood_does_not_trigger_whisper(self):
        g = FakeEngine("google", Transcript("", None))
        w = FakeEngine("whisper", Transcript("x", 0.5))
        stt = self.make(g, w)
        self.assertEqual(stt.transcribe(self.audio).text, "")
        self.assertEqual(w.calls, 0)

    def test_no_fallback_setting(self):
        g = FakeEngine("google", error="offline")
        w = FakeEngine("whisper", Transcript("w", 0.5))
        stt = self.make(g, w, fallback_to_whisper=False)
        with self.assertRaises(SttError):
            stt.transcribe(self.audio)
        self.assertEqual(w.calls, 0)

    def test_both_fail_gives_friendly_error(self):
        stt = self.make(FakeEngine("google", error="no internet"),
                        FakeEngine("whisper", error="model missing"))
        with self.assertRaises(SttError) as cm:
            stt.transcribe(self.audio)
        self.assertIn("no internet", str(cm.exception))
        self.assertIn("model missing", str(cm.exception))

    def test_whisper_as_main_engine(self):
        g = FakeEngine("google", Transcript("g", 0.9))
        w = FakeEngine("whisper", Transcript("w", 0.5))
        stt = self.make(g, w, stt_engine="whisper")
        self.assertEqual(stt.transcribe(self.audio).text, "w")
        self.assertEqual(g.calls, 0)
        self.assertTrue(stt.needs_preload)

    def test_google_main_does_not_preload_whisper(self):
        stt = self.make(FakeEngine("google"), FakeEngine("whisper"))
        self.assertFalse(stt.needs_preload)

    def test_unknown_engine_name_uses_google(self):
        g = FakeEngine("google", Transcript("g", 0.9))
        stt = self.make(g, FakeEngine("whisper"), stt_engine="banana")
        self.assertEqual(stt.transcribe(self.audio).text, "g")


if __name__ == "__main__":
    unittest.main()
