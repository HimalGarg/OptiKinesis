import threading
import unittest
from array import array

import speech_recognition as sr

from speech_input import Endpointer, SpeechInputError, frame_rms, record_utterance, transcribe

RATE = 16000
FRAME = int(RATE * 0.03)


def tone(seconds, amplitude):
    count = int(RATE * seconds)
    return array("h", ((amplitude if i % 2 else -amplitude) for i in range(count))).tobytes()


class FakeStream:
    def __init__(self, audio):
        self.audio = audio
        self.pos = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, frames):
        chunk = self.audio[self.pos:self.pos + frames * 2]
        self.pos += frames * 2
        return chunk.ljust(frames * 2, b"\0"), False


class FakeRecognizer:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def recognize_google(self, audio, language=None):
        self.calls.append((audio.sample_rate, language))
        if self.error:
            raise self.error
        return self.result


class EndpointerTests(unittest.TestCase):
    def feed_until_stop(self, endpointer, levels):
        for level in levels:
            if endpointer.feed(level):
                return True
        return False

    def test_stops_after_a_pause_following_speech(self):
        ep = Endpointer()
        levels = [50] * 10 + [3000] * 40 + [50] * 100
        self.assertTrue(self.feed_until_stop(ep, levels))
        self.assertEqual(ep.reason, "pause")
        self.assertAlmostEqual(ep.elapsed, (10 + 40) * 0.03 + 1.11, delta=0.06)

    def test_gives_up_when_nothing_is_said(self):
        ep = Endpointer(no_speech_s=1.0)
        self.assertTrue(self.feed_until_stop(ep, [50] * 200))
        self.assertEqual(ep.reason, "no_speech")
        self.assertFalse(ep.heard_speech)

    def test_long_speech_is_capped(self):
        ep = Endpointer(max_s=2.0)
        self.assertTrue(self.feed_until_stop(ep, [3000] * 500))
        self.assertEqual(ep.reason, "max_length")

    def test_noisy_room_raises_the_threshold(self):
        ep = Endpointer()
        self.feed_until_stop(ep, [600] * 10)
        self.assertEqual(ep.threshold, 1500)  # capped at normal speaking level
        self.assertFalse(ep.feed(700))  # background noise is not speech
        self.assertFalse(ep.heard_speech)

    def test_speaking_immediately_is_not_cut_off(self):
        ep = Endpointer()
        levels = [3000] * 60 + [50] * 100  # talks from the first moment for 1.8 s
        self.assertTrue(self.feed_until_stop(ep, levels))
        self.assertEqual(ep.reason, "pause")
        self.assertGreater(ep.elapsed, 1.8 + 1.0)

    def test_frame_rms(self):
        self.assertAlmostEqual(frame_rms(array("h", [100, -100, 100, -100])), 100.0)


class RecordingTests(unittest.TestCase):
    def test_records_one_utterance_from_the_stream(self):
        audio = tone(0.5, 40) + tone(1.0, 4000) + tone(3.0, 40)
        pcm = record_utterance(threading.Event(), stream_factory=lambda: FakeStream(audio))
        seconds = len(pcm) / 2 / RATE
        self.assertGreater(seconds, 2.4)
        self.assertLess(seconds, 2.9)

    def test_silence_raises_no_speech(self):
        audio = tone(10.0, 40)
        with self.assertRaises(SpeechInputError) as ctx:
            record_utterance(threading.Event(), stream_factory=lambda: FakeStream(audio))
        self.assertEqual(ctx.exception.code, "no_speech")

    def test_stop_button_ends_recording(self):
        stop = threading.Event()
        stop.set()
        pcm = record_utterance(stop, stream_factory=lambda: FakeStream(tone(5.0, 4000)))
        self.assertEqual(pcm, b"")

    def test_microphone_failure_is_explained(self):
        def broken():
            raise OSError("no device")
        with self.assertRaises(SpeechInputError) as ctx:
            record_utterance(threading.Event(), stream_factory=broken)
        self.assertEqual(ctx.exception.code, "mic_error")


class TranscribeTests(unittest.TestCase):
    def test_returns_clean_text(self):
        recognizer = FakeRecognizer(result="  find   exercises for ALS ")
        self.assertEqual(transcribe(b"\0\0" * 100, recognizer=recognizer), "find exercises for ALS")
        self.assertEqual(recognizer.calls, [(RATE, "en-US")])

    def test_unclear_speech_and_service_errors(self):
        with self.assertRaises(SpeechInputError) as ctx:
            transcribe(b"\0\0" * 100, recognizer=FakeRecognizer(error=sr.UnknownValueError()))
        self.assertEqual(ctx.exception.code, "unclear")
        with self.assertRaises(SpeechInputError) as ctx:
            transcribe(b"\0\0" * 100, recognizer=FakeRecognizer(error=sr.RequestError("offline")))
        self.assertIn("internet", ctx.exception.message)

    def test_empty_audio_is_rejected(self):
        with self.assertRaises(SpeechInputError):
            transcribe(b"", recognizer=FakeRecognizer(result="x"))


if __name__ == "__main__":
    unittest.main()
