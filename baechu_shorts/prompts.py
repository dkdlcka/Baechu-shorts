"""AI 생성 모드용 프롬프트 팩: 컷별 키프레임(이미지) 프롬프트 + 이미지→영상(i2v) 프롬프트.

어떤 생성 도구를 쓰든(나노바나나/GPT 이미지/Flux Kontext → Kling/Veo/Hailuo/Runway)
같은 프롬프트를 그대로 붙여 넣을 수 있게 도구 중립적으로 만든다.
결과물을 <에피소드>/shots/scene_XX.png(또는 .mp4)로 저장하면 렌더러가 자동으로 사용한다.
"""
from __future__ import annotations

import json
from pathlib import Path

from .episode import Episode

SHOT_TEXT = {
    "wide": "medium-wide shot, full body visible, eye-level camera",
    "medium": "medium shot from the chest up, eye-level camera",
    "close": "close-up on the face, eye-level camera",
    "extreme_close": "extreme close-up on the face filling the frame",
}
MOVE_TEXT = {
    "push_in": "slow camera push-in",
    "pull_out": "slow camera pull-out",
    "pan_left": "gentle pan to the left",
    "pan_right": "gentle pan to the right",
    "punch": "quick snap zoom-in at the start, then hold",
    "shake": "slight handheld shake, comedic",
    "static": "locked-off static camera",
}
STYLE = ("photorealistic, cinematic lighting, 9:16 vertical composition, subject centered in the "
         "upper-middle of the frame, leave the lower third clean for subtitles, no text, no watermark")
NEGATIVE = "extra legs, extra ears, deformed paws, human hands, text, captions, logo, watermark, blurry face"


def build(ep: Episode) -> dict:
    ch = ep.character
    identity = f"{ch.name_en}, {ch.appearance}"
    pack = {
        "character_sheet": {
            "reference_images": [str(p) for p in ch.ref_images],
            "prompt": (f"Character turnaround sheet of {identity}, {ep.wardrobe}. Front, three-quarter and "
                       f"side views, neutral studio background. "
                       f"Keep the exact fur colors, face shape and eyes from the reference photo."),
        },
        "scenes": [],
    }
    for s in ep.scenes:
        v = ep.voices[s.speaker]
        speaking = not v.offscreen
        action = (f"{ch.name_en} is speaking to the camera, mouth moving naturally"
                  if speaking else f"{ch.name_en} is listening to an off-screen interviewer, mouth closed")
        pack["scenes"].append({
            "file": f"shots/scene_{s.index:02d}.png",
            "video_file": f"shots/scene_{s.index:02d}.mp4",
            "image_prompt": (f"{identity}, {ep.wardrobe}. {s.expression}. {ep.setting}. "
                             f"{SHOT_TEXT[s.shot]}. {STYLE}"),
            "negative_prompt": NEGATIVE,
            "video_prompt": (f"{action}. {s.expression}. {MOVE_TEXT[s.move]}. Keep the character identical "
                             f"to the first frame. 3-5 seconds."),
            # Veo 3처럼 음성까지 만드는 모델이면 대사를 그대로 넣고, 아니면 TTS 음성으로 립싱크
            "dialogue": {"speaker": v.label, "ko": s.ko, "en": s.en, "onscreen": speaking},
        })
    return pack


def write(ep: Episode, out_dir: Path) -> Path:
    pack = build(ep)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "prompts.json").write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# 프롬프트 팩 — {ep.title}\n",
             "## 0. 캐릭터 시트 (한 번만)\n",
             f"레퍼런스: `{', '.join(Path(p).name for p in pack['character_sheet']['reference_images'])}`\n",
             f"```\n{pack['character_sheet']['prompt']}\n```\n"]
    for i, sc in enumerate(pack["scenes"]):
        d = sc["dialogue"]
        lines += [f"## 컷 {i:02d} — {d['speaker']}: “{d['ko']}”\n",
                  f"**키프레임 이미지** → `{sc['file']}`\n", f"```\n{sc['image_prompt']}\n```\n",
                  f"**영상(i2v)** → `{sc['video_file']}`\n", f"```\n{sc['video_prompt']}\n```\n"]
    lines.append(f"\n네거티브 프롬프트: `{NEGATIVE}`\n")
    path = out_dir / "prompts.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
