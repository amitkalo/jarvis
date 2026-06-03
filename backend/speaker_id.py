"""
Speaker verification for Jarvis — only act on the enrolled owner's voice.

Uses Resemblyzer (a lightweight d-vector speaker encoder) to turn a short
utterance into a 256-d embedding, then compares it (cosine similarity) to the
enrolled owner's reference embedding stored in jarvis_voiceprint.npy.

Flow:
    enroll_voice.py  → records ~15s of the owner → saves jarvis_voiceprint.npy
    SpeakerGate      → on every utterance, VoiceID.verify() decides accept/reject

If no voiceprint exists, verify() returns (True, 1.0) so Jarvis stays usable
(fails open — never locks the user out before they've enrolled).
"""
from pathlib import Path
from typing import List, Tuple, Optional

import numpy as np

_VOICEPRINT_PATH = Path(__file__).parent.parent / "jarvis_voiceprint.npy"

# Cosine-similarity threshold. In practice on this mic the owner scores ~0.60-0.70
# and other speakers ~0.45-0.55, so 0.57 sits in the gap: owner passes, others fail.
# Raise toward 0.65 to be stricter (may reject the owner on short words),
# lower toward 0.50 to be more permissive (may let similar voices through).
DEFAULT_THRESHOLD = 0.57


class VoiceID:
    """Lazy-loaded speaker encoder + enrolled reference embedding."""

    def __init__(self, threshold: float = DEFAULT_THRESHOLD) -> None:
        self.threshold = threshold
        self._encoder = None                 # loaded on first use
        self._ref: Optional[np.ndarray] = None
        if _VOICEPRINT_PATH.exists():
            try:
                self._ref = np.load(_VOICEPRINT_PATH)
            except Exception:
                self._ref = None

    # ── internals ────────────────────────────────────────────────────────────
    def _enc(self):
        if self._encoder is None:
            from resemblyzer import VoiceEncoder
            self._encoder = VoiceEncoder("cpu")
        return self._encoder

    def _embed(self, pcm_float: np.ndarray, sr: int) -> Optional[np.ndarray]:
        """
        Trim silence, resample to 16k, return a unit-length 256-d embedding.
        Returns None if there isn't enough voiced audio.
        """
        from resemblyzer import preprocess_wav
        try:
            wav = preprocess_wav(pcm_float, source_sr=sr)
        except Exception:
            return None
        if wav is None or len(wav) < 16000 * 0.4:   # <0.4s of voiced speech
            return None
        return self._enc().embed_utterance(wav)

    def voiced_seconds(self, pcm_float: np.ndarray, sr: int) -> float:
        """Return the duration (s) of actual voiced speech after silence trimming."""
        from resemblyzer import preprocess_wav
        try:
            wav = preprocess_wav(pcm_float, source_sr=sr)
        except Exception:
            return 0.0
        return len(wav) / 16000.0 if wav is not None else 0.0

    # ── public API ───────────────────────────────────────────────────────────
    def is_enrolled(self) -> bool:
        return self._ref is not None

    def verify(self, pcm_float: np.ndarray, sr: int) -> Tuple[bool, float]:
        """
        Compare an utterance to the enrolled owner.
        Returns (is_owner, similarity_score).
        Fails OPEN: if not enrolled or audio too short to judge → (True, 1.0).
        """
        if self._ref is None:
            return True, 1.0
        emb = self._embed(pcm_float, sr)
        if emb is None:
            return True, 1.0                 # not enough audio to judge → don't block
        score = float(
            np.dot(emb, self._ref)
            / (np.linalg.norm(emb) * np.linalg.norm(self._ref) + 1e-9)
        )
        return score >= self.threshold, score

    def enroll(self, pcm_floats: List[np.ndarray], sr: int) -> int:
        """
        Build a reference embedding from one or more recordings of the owner.
        Averages per-clip embeddings, normalises, saves to jarvis_voiceprint.npy.
        Returns the number of clips successfully used.
        """
        embs = []
        for clip in pcm_floats:
            e = self._embed(clip, sr)
            if e is not None:
                embs.append(e)
        if not embs:
            raise RuntimeError("No usable speech found for enrollment — speak louder/longer.")
        ref = np.mean(embs, axis=0)
        ref = ref / (np.linalg.norm(ref) + 1e-9)
        np.save(_VOICEPRINT_PATH, ref)
        self._ref = ref
        return len(embs)
