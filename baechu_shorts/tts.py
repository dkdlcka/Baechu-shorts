"""대사 → 음성 파일. 기본은 edge-tts(무료), 실패 시 무음 + 글자수 기반 길이로 대체."""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import subprocess
from pathlib import Path

import numpy as np

from .episode import Voice
from .tools import CACHE_DIR, ffmpeg

SR = 44100


def _patch_edge_tts_ssl() -> None:
    # 사내 프록시처럼 자체 CA를 쓰는 환경: SSL_CERT_FILE을 edge-tts에도 적용
    cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if cafile:
        import edge_tts.communicate as c

        c._SSL_CTX = ssl.create_default_context(cafile=cafile)


def decode(path: Path) -> np.ndarray:
    """오디오 파일 → float32 mono @ 44.1kHz."""
    raw = subprocess.run(
        [ffmpeg(), "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def _trim_silence(x: np.ndarray, thresh: float = 0.01, pad: float = 0.04) -> np.ndarray:
    idx = np.flatnonzero(np.abs(x) > thresh)
    if idx.size == 0:
        return x
    p = int(pad * SR)
    return x[max(0, idx[0] - p): idx[-1] + p]


def synthesize(text: str, voice: Voice) -> np.ndarray:
    """텍스트를 합성해 파형을 돌려준다. 결과는 캐시된다."""
    key = hashlib.sha1(json.dumps([text, voice.voice, voice.rate, voice.pitch]).encode()).hexdigest()[:16]
    out = CACHE_DIR / "tts" / f"{key}.mp3"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            import edge_tts

            _patch_edge_tts_ssl()
            edge_tts.Communicate(text, voice.voice, rate=voice.rate, pitch=voice.pitch).save_sync(str(out))
        except Exception as e:  # 네트워크 불가 등 → 무음으로 진행
            print(f"[tts] 합성 실패({type(e).__name__}: {e}) → 무음으로 대체")
            out.unlink(missing_ok=True)
            return np.zeros(int(SR * (0.6 + 0.12 * len(text))), dtype=np.float32)
    return _trim_silence(decode(out))
