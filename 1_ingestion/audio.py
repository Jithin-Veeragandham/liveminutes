"""Cutting a live audio stream into pieces at pauses, so each piece can be uploaded to Groq.

Audio arrives as small chunks of 16-bit mono PCM (raw samples, about 10-20 ms each).
One Speaker per stream: a participant on Meet, or the single mixed stream on Teams.
"""

import io
import math
import wave
from array import array
from collections import deque

import settings


def loudness_dbfs(pcm):
    """Volume of a chunk in dBFS: 0 is the loudest possible, silence is around -90."""
    samples = array("h", pcm)  # bytes -> 16-bit numbers
    if not samples:
        return -99.0
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    return 20 * math.log10(rms / 32768) if rms > 0 else -99.0


def duration_ms(pcm):
    return len(pcm) / 2 / settings.RATE * 1000  # 2 bytes per sample


def voiced_ms(pcm):
    """How much of this audio is speech, measured in 20 ms steps."""
    step = int(settings.RATE * 0.02) * 2  # bytes in 20 ms
    return sum(20 for i in range(0, len(pcm), step) if loudness_dbfs(pcm[i:i + step]) > settings.SPEECH_DBFS)


def wav_bytes(pcm):
    """Raw PCM -> a .wav file in memory (what Groq expects)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(settings.RATE)
        w.writeframes(pcm)
    return buf.getvalue()


class Piece:
    """A stretch of speech from one stream, ready to transcribe."""

    def __init__(self, uuid, start_ms):
        self.uuid = uuid                # which stream (participant id, or "mixed" on Teams)
        self.start_ms = start_ms        # wall clock when speech started
        self.speech_end_ms = start_ms   # wall clock of the last loud chunk
        self.pcm = bytearray()
        self.pcm_start_ms = start_ms    # wall clock of the first byte of pcm (earlier if there is pre-roll)
        self.voiced_ms = 0              # how much of it is actually speech
        self.label = None               # Teams only: who spoke, worked out later from speech events
        self.merged = 1                 # how many pieces went into this upload
        self.n = None                   # upload number in the session (pieces/NNNN.wav)
        self.ready_ms = None            # when it joined the upload queue
        self.talk_ms = None             # Teams only: ms each person talked in it, for pieces.jsonl

    @property
    def seconds(self):
        return duration_ms(self.pcm) / 1000

    def merge(self, other):
        """Append another piece (queued behind the rate limit) with 0.2 s of silence in between."""
        self.pcm += bytes(int(settings.RATE * 0.2) * 2) + other.pcm
        self.speech_end_ms = other.speech_end_ms
        self.voiced_ms += other.voiced_ms
        self.merged += 1


class Speaker:
    """Cuts one stream into pieces: a piece starts when it gets loud and ends at a pause."""

    def __init__(self, uuid):
        self.uuid = uuid
        self.piece = None        # the piece being built, None while quiet
        self.quiet_ms = 0        # quiet time since the last loud chunk
        self.preroll = deque()   # the last few quiet chunks, so the first syllable isn't clipped
        self.prompt = ""         # this stream's last words, sent to Whisper as context

    def add(self, pcm, now_ms):
        """Feed one chunk. Returns a finished Piece, or None."""
        dur = duration_ms(pcm)
        loud = loudness_dbfs(pcm) > settings.SPEECH_DBFS

        if self.piece is None:  # quiet so far
            if not loud:
                self.preroll.append(pcm)
                while len(self.preroll) * dur > settings.PREROLL_MS:
                    self.preroll.popleft()
                return None
            # speech starts: new piece, beginning with the pre-roll
            self.piece = Piece(self.uuid, int(now_ms - dur))
            self.piece.pcm += b"".join(self.preroll)
            self.piece.pcm_start_ms = self.piece.start_ms - int(duration_ms(self.piece.pcm))
            self.preroll.clear()
            self.quiet_ms = 0

        self.piece.pcm += pcm
        if loud:
            self.quiet_ms = 0
            self.piece.speech_end_ms = now_ms
            self.piece.voiced_ms += dur
        else:
            self.quiet_ms += dur

        pause = self.quiet_ms >= settings.PAUSE_MS and self.piece.seconds >= settings.MIN_PIECE_S
        end_of_turn = self.quiet_ms >= settings.END_OF_TURN_MS
        too_long = self.piece.seconds >= settings.MAX_PIECE_S
        if pause or end_of_turn or too_long:
            return self.flush()
        return None

    def flush(self):
        """End the current piece now. Returns it, or None if there was nothing worth sending."""
        piece, self.piece = self.piece, None
        self.quiet_ms = 0
        if piece is None or piece.voiced_ms < settings.MIN_VOICED_MS:
            return None  # clicks or noise, not speech
        return piece

    def split(self, at_ms):
        """Teams: someone else started talking at wall-clock time at_ms. End the current piece
        there and keep building the rest as a new piece. Returns the first part, or None
        (no cut) if it would hold too little speech to send on its own."""
        piece = self.piece
        if piece is None:
            return None
        cut = int((at_ms - piece.pcm_start_ms) * settings.RATE / 1000) * 2  # in bytes, whole samples
        if cut <= 0 or cut >= len(piece.pcm):
            return None
        first_voiced = voiced_ms(piece.pcm[:cut])
        if first_voiced < settings.MIN_VOICED_MS:
            return None  # keep it with the rest rather than lose it

        rest = Piece(piece.uuid, at_ms)
        rest.pcm = piece.pcm[cut:]
        rest.voiced_ms = max(0, piece.voiced_ms - first_voiced)
        rest.speech_end_ms = max(at_ms, piece.speech_end_ms)
        self.piece = rest

        piece.pcm = piece.pcm[:cut]
        piece.voiced_ms = first_voiced
        piece.speech_end_ms = min(piece.speech_end_ms, at_ms)
        return piece

    def discard(self):
        """Forget everything buffered (consent was withdrawn)."""
        self.piece = None
        self.quiet_ms = 0
        self.preroll.clear()
