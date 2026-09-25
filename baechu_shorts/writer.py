"""(선택) Claude로 에피소드 대본 자동 생성: 주제 한 줄 + 캐릭터 사진 → episode.yaml."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from .episode import load_character

MODEL = "claude-opus-5"


class DraftScene(BaseModel):
    speaker: str = Field(description="'main' = 주인공 강아지, 그 외는 상대역 key")
    ko: str = Field(description="한국어 대사. 한 컷에 한 문장, 25자 안팎")
    en: str = Field(description="영어 자막 번역")
    shot: Literal["wide", "medium", "close", "extreme_close"]
    move: Literal["push_in", "pull_out", "pan_left", "pan_right", "punch", "shake", "static"]
    expression: str = Field(description="주인공의 표정/동작 (영어, 이미지 생성용)")


class DraftEpisode(BaseModel):
    title: str = Field(description="업로드 제목, 이모지 1개 포함, 20자 이내")
    top_title: str = Field(description="영상 상단 고정 제목")
    description: str = Field(description="3줄 설명. 주인공을 제3자가 소개하는 말투")
    hashtags: list[str]
    setting: str = Field(description="배경 묘사 (영어)")
    wardrobe: str = Field(description="주인공 의상과 자세 (영어)")
    partner_key: str = Field(description="상대역 key (영문 소문자)")
    partner_label: str = Field(description="상대역 한국어 호칭, 예: 면접관, 선배, 손님")
    scenes: list[DraftScene]


SYSTEM = """너는 강아지가 사람 직업/상황에 놓인 '의인화 상황극' 쇼츠의 대본 작가다.
형식 규칙:
- 30~45초, 10~14컷. 한 컷 = 한 대사. 주인공과 상대역(화면 밖 목소리)이 번갈아 말한다.
- 1컷에서 상황을 바로 제시(훅). 진지한 톤으로 시작해 강아지 본능(간식, 산책, 냄새, 꼬리)이 튀어나오는 반전.
- 마지막 컷은 결과를 한마디로 정리하는 펀치라인. 반전 대사는 punch/shake + close/extreme_close.
- 대사는 자연스러운 구어체 한국어. 주인공 말투는 캐릭터 설정을 따른다."""


def write_episode(topic: str, character_path: Path, out_path: Path) -> Path:
    import anthropic

    ch = load_character(character_path)
    img = ch.ref_images[0]
    media = "image/png" if img.suffix.lower() == ".png" else "image/jpeg"
    client = anthropic.Anthropic()
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media,
                                             "data": base64.standard_b64encode(img.read_bytes()).decode()}},
                {"type": "text", "text": (f"주인공: {ch.name_ko} (사진 참고)\n외형: {ch.appearance}\n"
                                          f"성격/말투: {ch.personality}\n\n주제: {topic}")},
            ],
        }],
        output_format=DraftEpisode,
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude가 요청을 거절했습니다. 주제를 바꿔 다시 시도하세요.")
    d = resp.parsed_output

    rel_char = Path(__import__("os").path.relpath(character_path.resolve(), out_path.resolve().parent))
    doc = {
        "title": d.title, "top_title": d.top_title, "watermark": f"{ch.name_ko} {ch.name_en.upper()}",
        "character": str(rel_char), "description": d.description, "hashtags": d.hashtags,
        "setting": d.setting, "wardrobe": d.wardrobe,
        "voices": {
            "main": {"from_character": True},
            d.partner_key: {"label": d.partner_label, "offscreen": True,
                            "voice": "ko-KR-InJoonNeural", "rate": "-4%", "pitch": "-6Hz"},
        },
        "audio": {"bgm": "synth", "bgm_volume": 0.12},
        "scenes": [s.model_dump() for s in d.scenes],
    }
    for s in doc["scenes"]:
        if s["speaker"] not in doc["voices"]:
            s["speaker"] = d.partner_key
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return out_path
