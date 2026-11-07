"""Run faster-whisper (small, CPU) on each participant WAV and print
"<speaker name>: <text>" in time order across all speakers.

Usage (from 1_ingestion/): .venv/bin/python tools/receiver/transcribe.py [tools/receiver/sessions/<bot_id>] [--mixed] [--language en]
Default session: the most recently modified one.
"""

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel

HERE = Path(__file__).resolve().parent


def load_wav(path):
    """receiver.py always writes 16 kHz mono int16; read it directly instead of
    going through PyAV (newer PyAV breaks faster-whisper's decode_audio)."""
    with wave.open(str(path)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1, f"{path.name} is not 16 kHz mono"
        pcm = w.readframes(w.getnframes())
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def mmss(seconds):
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("session", nargs="?", help="sessions/<bot_id> folder")
    parser.add_argument("--mixed", action="store_true", help="also transcribe mixed.wav")
    parser.add_argument("--language", default=None, help="e.g. en (default: auto-detect)")
    args = parser.parse_args()

    if args.session:
        session = Path(args.session)
    else:
        sessions = sorted((HERE / "sessions").glob("*/participants.json"), key=lambda p: p.stat().st_mtime)
        if not sessions:
            sys.exit("no sessions found in tools/receiver/sessions/")
        session = sessions[-1].parent
    manifest = json.loads((session / "participants.json").read_text())
    names = {uuid: p.get("name") or uuid for uuid, p in manifest["participants"].items()}
    names["mixed"] = "MIXED"
    # receiver.py makes uuids filename-safe, so map files back through the manifest
    speaker_for_file = {fname: names.get(key, key) for key, fname in manifest.get("wav_files", {}).items()}

    wavs = sorted(p for p in session.glob("*.wav") if args.mixed or p.name != "mixed.wav")
    print(f"session {session.name}: {len(wavs)} file(s)\n", file=sys.stderr)

    model = WhisperModel("small", device="cpu", compute_type="int8", download_root=str(HERE / "models"))
    lines = []
    for wav in wavs:
        speaker = speaker_for_file.get(wav.name) or names.get(wav.stem, f"?{wav.stem}")
        # vad_filter skips the silence padding the receiver adds between turns
        segments, _ = model.transcribe(load_wav(wav), language=args.language, vad_filter=True, beam_size=5)
        for seg in segments:
            text = seg.text.strip()
            if text:
                lines.append((seg.start, speaker, text))
        print(f"  done: {speaker} ({wav.name})", file=sys.stderr)

    print(file=sys.stderr)
    for start, speaker, text in sorted(lines):
        print(f"[{mmss(start)}] {speaker}: {text}")


if __name__ == "__main__":
    main()
