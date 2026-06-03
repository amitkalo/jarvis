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


def _open_stream(p, device_index, rate):
    """Try to open the mic at a given sample rate; return stream or raise."""
    import pyaudio
    kwargs = dict(format=pyaudio.paInt16, channels=1, rate=rate,
                  input=True, frames_per_buffer=CHUNK)
    if device_index is not None:
        kwargs["input_device_index"] = device_index
    return p.open(**kwargs)


def record(seconds: int, device_index: int | None) -> tuple[np.ndarray, int]:
    """
    Record from the mic. Many mics (e.g. Intel SST/WASAPI) refuse 16 kHz, so we
    detect the device's native rate and fall back through common rates.
    Returns (float32_pcm, actual_sample_rate). Resampling happens later.
    """
    import pyaudio
    p = pyaudio.PyAudio()

    # Build a candidate list: device's default rate first, then common rates
    candidates = []
    try:
        if device_index is not None:
            info = p.get_device_info_by_index(device_index)
            candidates.append(int(info.get("defaultSampleRate", 48000)))
    except Exception:
        pass
    for r in (48000, 44100, 32000, 16000):
        if r not in candidates:
            candidates.append(r)

    stream = None
    rate   = None
    for r in candidates:
        try:
            stream = _open_stream(p, device_index, r)
            rate = r
            break
        except Exception:
            stream = None
    if stream is None:
        p.terminate()
        raise RuntimeError(f"Could not open mic at any of {candidates} Hz")

    print(f"  Recording at {rate} Hz")

    # Get-ready countdown so you're not caught mid-breath
    print("\n  Get ready to speak...")
    for c in (3, 2, 1):
        print(f"     {c}...", end="\r", flush=True)
        time.sleep(1)
    print(f"\n>> RECORDING {seconds}s - talk naturally NOW "
          f"(read anything, keep speaking)...\n")

    frames = []
    total_chunks = int(rate / CHUNK * seconds)
    for i in range(total_chunks):
        data = stream.read(CHUNK, exception_on_overflow=False)
        frames.append(data)
        if i % int(rate / CHUNK) == 0:
            secs_left = seconds - i // int(rate / CHUNK)
            print(f"   ...{secs_left}s left", end="\r", flush=True)
    stream.stop_stream(); stream.close(); p.terminate()
    print("   ...done.            ")

    pcm = np.frombuffer(b"".join(frames), dtype=np.int16).astype(np.float32) / 32768.0
    return pcm, rate


def main() -> None:
    device_index = _load_mic_index()
    print("=" * 55)
    print("  JARVIS - Voice Enrollment")
    print("=" * 55)
    print(f"  Mic device index: {device_index if device_index is not None else 'default'}")

    try:
        pcm, actual_sr = record(RECORD_SECS, device_index)
    except Exception as exc:
        print(f"\n[ERROR] Recording failed: {exc}")
        print("        Check your mic, or that jarvis_mic.json points to a working device.")
        sys.exit(1)

    # Split into 3 overlapping clips -> more robust averaged embedding
    n = len(pcm)
    clips = [pcm, pcm[: n // 2], pcm[n // 2:]]

    from speaker_id import VoiceID
    vid = VoiceID()
    print("\n  Building voiceprint...")
    try:
        used = vid.enroll(clips, actual_sr)   # VoiceID resamples to 16k internally
    except Exception as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    print(f"\n  [OK] Voiceprint saved from {used} clip(s) -> jarvis_voiceprint.npy")
    print("  [OK] Jarvis will now only respond to YOUR voice.")
    print("\n  (Delete jarvis_voiceprint.npy to turn speaker lock off.)")
    print("=" * 55)


if __name__ == "__main__":
    main()
