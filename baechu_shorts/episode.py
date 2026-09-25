"""에피소드 대본(YAML) 로딩과 검증."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

SHOTS = {"wide", "medium", "close", "extreme_close"}
MOVES = {"push_in", "pull_out", "pan_left", "pan_right", "punch", "shake", "static"}


@dataclass
class Voice:
    key: str
    voice: str
    rate: str = "+0%"
    pitch: str = "+0Hz"
    label: str = ""
    offscreen: bool = False


@dataclass
class Scene:
    index: int
    speaker: str
    ko: str
    en: str = ""
    shot: str = "medium"
    move: str = "push_in"
    expression: str = ""
    sfx: str | None = None
    stamp: str | None = None
    hold: float = 0.35  # 대사 끝난 뒤 여유 시간(초)


@dataclass
class Character:
    id: str
    name_ko: str
    name_en: str
    ref_images: list[Path]
    face_box: tuple[float, float, float, float]
    body_center: tuple[float, float]
    appearance: str
    personality: str
    voice: dict


@dataclass
class Episode:
    path: Path
    title: str
    top_title: str
    watermark: str
    description: str
    hashtags: list[str]
    setting: str
    wardrobe: str
    character: Character
    voices: dict[str, Voice]
    scenes: list[Scene]
    audio: dict = field(default_factory=dict)

    @property
    def dir(self) -> Path:
        return self.path.parent


def load_character(path: Path) -> Character:
    d = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Character(
        id=d["id"],
        name_ko=d["name_ko"],
        name_en=d.get("name_en", d["id"]),
        ref_images=[(path.parent / p).resolve() for p in d["ref_images"]],
        face_box=tuple(d.get("face_box", [0.3, 0.2, 0.7, 0.5])),
        body_center=tuple(d.get("body_center", [0.5, 0.55])),
        appearance=d.get("appearance", ""),
        personality=d.get("personality", ""),
        voice=d.get("voice", {}),
    )


def load_episode(path: str | Path) -> Episode:
    path = Path(path).resolve()
    d = yaml.safe_load(path.read_text(encoding="utf-8"))
    character = load_character((path.parent / d["character"]).resolve())

    voices: dict[str, Voice] = {}
    for key, v in (d.get("voices") or {}).items():
        v = dict(v or {})
        if v.pop("from_character", False):
            v = {**{k: character.voice[k] for k in ("voice", "rate", "pitch") if k in character.voice}, **v}
            v.setdefault("label", character.name_ko)
        voices[key] = Voice(key=key, voice=v["voice"], rate=v.get("rate", "+0%"),
                            pitch=v.get("pitch", "+0Hz"), label=v.get("label", key),
                            offscreen=bool(v.get("offscreen", False)))

    scenes = []
    for i, s in enumerate(d["scenes"]):
        scene = Scene(index=i, **s)
        if scene.speaker not in voices:
            raise ValueError(f"scene {i}: 알 수 없는 speaker '{scene.speaker}' (voices에 정의 필요)")
        if scene.shot not in SHOTS:
            raise ValueError(f"scene {i}: shot은 {sorted(SHOTS)} 중 하나여야 함")
        if scene.move not in MOVES:
            raise ValueError(f"scene {i}: move는 {sorted(MOVES)} 중 하나여야 함")
        scenes.append(scene)

    return Episode(
        path=path,
        title=d["title"],
        top_title=d.get("top_title", ""),
        watermark=d.get("watermark", ""),
        description=d.get("description", ""),
        hashtags=list(d.get("hashtags", [])),
        setting=d.get("setting", ""),
        wardrobe=d.get("wardrobe", ""),
        character=character,
        voices=voices,
        scenes=scenes,
        audio=d.get("audio", {}),
    )
