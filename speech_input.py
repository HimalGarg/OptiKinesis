"""Voice typing for the on-screen keyboard.

One press of the keyboard's mic button records a single utterance from the
default microphone, stops automatically after a short pause (or when pressed
again), and returns the recognized text.

* Recording uses ``sounddevice`` (PortAudio), so PyAudio is not needed.
* Recognition uses the ``SpeechRecognition`` package with Google's free web
  speech service (the same engine as Chrome's speech input used by the
  ``--web`` dashboard).  It needs an internet connection, and the recorded
  audio is sent to Google.
* ``Endpointer`` decides when to stop; it is pure Python and unit tested.
"""

from __future__ import annotations

import logging
import math
import threading
from array import array
from typing import Callable, Optional

logger = logging.getLogger("optikinesis.speech")

SAMPLE_RATE = 16000
FRAME_S = 0.03
END_SILENCE_S = 1.1        # stop this long after the user stops talking
NO_SPEECH_TIMEOUT_S = 6.0  # give up if nothing is said
MAX_UTTERANCE_S = 15.0
LANGUAGE = "en-US"


class SpeechInputError(Exception):
    """A failure with a message that is safe and clear to show the user."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.message = message
        self.code = code


def frame_rms(samples) -> float:
    """Root-mean-square level of 16-bit samples."""
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class Endpointer:
    """Energy-based start/end detection for one spoken utterance.

    The first ``calibration_s`` of audio sets the background noise level;
    frames well above it count as speech.  ``feed`` returns True once the
    recording should stop, and ``reason`` says why.
    """

    def __init__(
        self,
        frame_s: float = FRAME_S,
        end_silence_s: float = END_SILENCE_S,
        no_speech_s: float = NO_SPEECH_TIMEOUT_S,
        max_s: float = MAX_UTTERANCE_S,
        calibration_s: float = 0.3,
        min_threshold: float = 350.0,
        noise_ratio: float = 3.0,
        max_threshold: float = 1500.0,
    ):
        self.frame_s = frame_s
        self.end_silence_s = end_silence_s
        self.no_speech_s = no_speech_s
        self.max_s = max_s
        self.calibration_frames = max(1, int(round(calibration_s / frame_s)))
        self.min_threshold = min_threshold
        self.noise_ratio = noise_ratio
        self.max_threshold = max_threshold
        self._calibration = []
        self.threshold: Optional[float] = None
        self.elapsed = 0.0
        self.heard_speech = False
        self._silence = 0.0
        self.reason = ""

    def feed(self, rms: float) -> bool:
        self.elapsed += self.frame_s
        if self.threshold is None:
            # Still measuring background noise: only clearly loud sound counts
            # as speech, so a noisy room isn't mistaken for talking.
            self._calibration.append(rms)
            loud = self.min_threshold * self.noise_ratio
            if len(self._calibration) >= self.calibration_frames:
                # Ignore frames that were already speech (the user may start
                # talking at once), and never let noise push the threshold
                # above normal speaking level.
                quiet = sorted(r for r in self._calibration if r < loud)
                noise = quiet[len(quiet) // 2] if quiet else 0.0
                self.threshold = min(self.max_threshold,
                                     max(self.min_threshold, noise * self.noise_ratio))
            threshold = loud
        else:
            threshold = self.threshold

        if rms >= threshold:
            self.heard_speech = True
            self._silence = 0.0
        elif self.heard_speech:
            self._silence += self.frame_s

        if self.heard_speech and self._silence >= self.end_silence_s:
            self.reason = "pause"
        elif not self.heard_speech and self.elapsed >= self.no_speech_s:
            self.reason = "no_speech"
        elif self.elapsed >= self.max_s:
            self.reason = "max_length"
        return bool(self.reason)


def record_utterance(
    stop_event: threading.Event,
    sample_rate: int = SAMPLE_RATE,
    endpointer: Optional[Endpointer] = None,
    stream_factory: Optional[Callable] = None,
) -> bytes:
    """Record one utterance as 16-bit mono PCM.  Ends on a pause or stop_event."""
    endpointer = endpointer or Endpointer()
    frame_len = int(sample_rate * endpointer.frame_s)
    if stream_factory is None:
        import sounddevice

        def stream_factory():
            return sounddevice.RawInputStream(
                samplerate=sample_rate, channels=1, dtype="int16", blocksize=frame_len
            )

    chunks = []
    try:
        with stream_factory() as stream:
            while not stop_event.is_set():
                data, _overflowed = stream.read(frame_len)
                data = bytes(data)
                chunks.append(data)
                if endpointer.feed(frame_rms(array("h", data))):
                    break
    except SpeechInputError:
        raise
    except Exception as exc:
        logger.warning("Microphone recording failed: %s", exc)
        raise SpeechInputError(
            "The microphone couldn't be opened. Check that one is connected and allowed.",
            code="mic_error",
        ) from exc

    if not endpointer.heard_speech and not stop_event.is_set():
        raise SpeechInputError("No speech heard. Press the mic and speak.", code="no_speech")
    return b"".join(chunks)


def transcribe(pcm: bytes, sample_rate: int = SAMPLE_RATE, recognizer=None,
               language: str = LANGUAGE) -> str:
    """Recognized text for 16-bit mono PCM audio."""
    import speech_recognition as sr

    if not pcm:
        raise SpeechInputError("No speech heard. Press the mic and speak.", code="no_speech")
    recognizer = recognizer or sr.Recognizer()
    audio = sr.AudioData(pcm, sample_rate, 2)
    try:
        text = recognizer.recognize_google(audio, language=language)
    except sr.UnknownValueError as exc:
        raise SpeechInputError("Didn't catch that. Please try again.", code="unclear") from exc
    except sr.RequestError as exc:
        logger.warning("Speech service request failed: %s", exc)
        raise SpeechInputError(
            "The speech service couldn't be reached. Check the internet connection.",
            code="service",
        ) from exc
    text = " ".join(str(text or "").split())
    if not text:
        raise SpeechInputError("Didn't catch that. Please try again.", code="unclear")
    return text


def listen_and_transcribe(stop_event: threading.Event,
                          on_recognizing: Optional[Callable[[], None]] = None) -> str:
    """Record one utterance from the default microphone and return its text."""
    pcm = record_utterance(stop_event)
    if on_recognizing is not None:
        on_recognizing()
    return transcribe(pcm)
