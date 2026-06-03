"""
Enroll the owner's voice so Jarvis only acts on YOUR speech.

Run once:
    python backend/enroll_voice.py

It records ~15 seconds while you talk naturally, builds a voiceprint, and
saves it to jarvis_voiceprint.npy.  After this, Jarvis ignores anyone whose
voice doesn't match.  To re-enroll (new mic, etc.), just run it again.
To disable speaker lock entirely, delete jarvis_voiceprint.npy.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT          = Path(__file__).parent.parent
MIC_CONFIG    = ROOT / "jarvis_mic.json"
RECORD_SECS   = 15
SAMPLE_RATE   = 16000
CHUNK         = 1024


def _load_mic_index() -> int | None:
    if MIC_CONFIG.exists():
        try:
            return int(json.loads(MIC_CONFIG.read_text()).get("index"))
        except Exception:
            pass
    return None


def record(seconds: int, device_index: int | None) -> np.ndarray:
    import pyaudio
    p = pyaudio.PyAudio()
    kwargs = dict(format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE,
                  input=True, frames_per_buffer=CHUNK)
    if device_index is not None:
        kwargs["input_device_index"] = device_index

    stream = p.open(**kwargs)
    frames = []
    total_chunks = int(SAMPLE_RATE / CHUNK * seconds)

    # Get-ready countdown so you're not caught mid-breath
    print("\n  Get ready to speak...")
    for c in (3, 2, 1):
        print(f"     {c}...", end="\r", flush=True)
        time.sleep(1)
    print(f"\n🎙  Recording {seconds}s — talk naturally NOW "
          f"(read anything, keep speaking)...\n")
    for i in range(total_chunks):
        data = stream.read(CHUNK, exception_on_overflow=False)
        frames.append(data)
        # simple progress bar
        if i % int(SAMPLE_RATE / CHUNK) == 0:
            secs_left = seconds - i // int(SAMPLE_RATE / CHUNK)
            print(f"   …{secs_left}s left", end="\r", flush=True)
    stream.stop_stream(); stream.close(); p.terminate()
    print("   …done.            ")

    pcm = np.frombuffer(b"".join(frames), dtype=np.int16).astype(np.float32) / 32768.0
    return pcm


def main() -> None:
    device_index = _load_mic_index()
    print("=" * 55)
    print("  JARVIS — Voice Enrollment")
    print("=" * 55)
    print(f"  Mic device index: {device_index if device_index is not None else 'default'}")

    try:
        pcm = record(RECORD_SECS, device_index)
    except Exception as exc:
        print(f"\n[ERROR] Recording failed: {exc}")
        print("        Check your mic, or that jarvis_mic.json points to a working device.")
        sys.exit(1)

    # Split into 3 overlapping clips → more robust averaged embedding
    n = len(pcm)
    clips = [pcm, pcm[: n // 2], pcm[n // 2:]]

    from speaker_id import VoiceID
    vid = VoiceID()
    print("\n  Building voiceprint...")
    try:
        used = vid.enroll(clips, SAMPLE_RATE)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    print(f"\n  ✓ Voiceprint saved from {used} clip(s) → jarvis_voiceprint.npy")
    print("  ✓ Jarvis will now only respond to YOUR voice.")
    print("\n  (Delete jarvis_voiceprint.npy to turn speaker lock off.)")
    print("=" * 55)


if __name__ == "__main__":
    main()
