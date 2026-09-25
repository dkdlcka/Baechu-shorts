"""오디오 트랙 조립: 대사 배치, BGM(합성 또는 파일), 효과음, 더킹, 노멀라이즈."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .tts import SR, decode

NOTE = {n: i for i, n in enumerate("C C# D D# E F F# G G# A A# B".split())}


def _hz(name: str, octave: int) -> float:
    return 440.0 * 2 ** ((NOTE[name] + 12 * (octave + 1) - 69) / 12)


def _pluck(freq: float, dur: float, decay: float = 5.0) -> np.ndarray:
    t = np.arange(int(dur * SR)) / SR
    env = np.exp(-decay * t) * np.minimum(1, t / 0.004)
    tone = np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(4 * np.pi * freq * t) + 0.12 * np.sin(6 * np.pi * freq * t)
    return (tone * env).astype(np.float32)


def synth_bgm(seconds: float, bpm: float = 112) -> np.ndarray:
    """가벼운 우쿨렐레풍 아르페지오 루프 (저작권 걱정 없는 기본 BGM)."""
    beat = 60 / bpm
    chords = [("C", "E", "G"), ("G", "B", "D"), ("A", "C", "E"), ("F", "A", "C")]
    out = np.zeros(int((seconds + 2) * SR), dtype=np.float32)
    bar = 0
    t = 0.0
    while t < seconds:
        chord = chords[bar % 4]
        for i in range(8):  # 한 마디 = 8분음표 8개
            pos = int((t + i * beat / 2) * SR)
            note = chord[[0, 1, 2, 1, 0, 2, 1, 2][i]]
            s = _pluck(_hz(note, 5), beat * 1.5) * 0.28
            out[pos:pos + len(s)] += s[: len(out) - pos]
            if i % 4 == 0:  # 베이스
                b = _pluck(_hz(chord[0], 3), beat * 2, decay=3) * 0.45
                out[pos:pos + len(b)] += b[: len(out) - pos]
        t += beat * 4
        bar += 1
    return out[: int(seconds * SR)]


def sfx_pop() -> np.ndarray:
    t = np.arange(int(0.12 * SR)) / SR
    f = 950 * np.exp(-18 * t) + 280
    return (np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-30 * t) * 0.6).astype(np.float32)


SFX = {"pop": sfx_pop}


def envelope(x: np.ndarray, fps: int, n_frames: int) -> np.ndarray:
    """프레임별 RMS(0~1). 말할 때 캐릭터를 들썩이게 하는 데 쓴다."""
    hop = SR / fps
    env = np.zeros(n_frames, dtype=np.float32)
    for i in range(n_frames):
        a, b = int(i * hop), int((i + 1) * hop)
        if a < len(x):
            seg = x[a:b]
            env[i] = float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0
    peak = env.max() or 1
    return np.clip(env / peak, 0, 1)


def mix(voice: np.ndarray, sfx_track: np.ndarray, bgm_cfg: str, bgm_volume: float, base_dir: Path) -> np.ndarray:
    n = len(voice)
    if bgm_cfg == "synth":
        bgm = synth_bgm(n / SR)
    elif bgm_cfg and bgm_cfg != "none":
        bgm = decode(base_dir / bgm_cfg)
        bgm = np.tile(bgm, int(np.ceil(n / len(bgm))))[:n]
    else:
        bgm = np.zeros(n, dtype=np.float32)
    bgm = bgm[:n] / (np.abs(bgm).max() or 1)

    # 대사가 나올 때 BGM을 살짝 줄인다(더킹)
    win = int(0.15 * SR)
    speaking = np.convolve(np.abs(voice), np.ones(win) / win, mode="same")
    duck = 1 - 0.45 * np.clip(speaking / (speaking.max() or 1) * 4, 0, 1)

    out = voice + sfx_track + bgm * bgm_volume * duck
    return (out / (np.abs(out).max() or 1) * 0.89).astype(np.float32)  # 약 -1 dBFS
