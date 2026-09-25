# 배추 쇼츠 파이프라인 설계

## 0. 레퍼런스 분석 — 「프로 선배가 되는 길 😎」 (김크루crewkim)

| 항목 | 관찰 내용 | 우리 파이프라인에 반영 |
|---|---|---|
| 포맷 | 1080×1920, 30fps, 60초 | 1080×1920 / 30fps, 30~45초 (완주율 우선) |
| 장르 | **의인화 상황극**: 포메라니안이 승무원 '선배'로 신입에게 업무를 가르치다가 스스로 실수 | 배추가 사람 직업/상황(면접, 알바, 회사원...)에 놓이는 시리즈 |
| 비주얼 | AI 생성 실사풍 캐릭터 + 기내 배경, 컷마다 캐릭터가 입을 움직이며 말함 (토킹헤드) | 캐릭터 시트 → 컷별 키프레임 → 이미지→영상(i2v) |
| 컷 구성 | 한 컷 = 한 대사, 2~4초 하드컷, 인물 번갈아 등장 | `scenes[]` 한 항목 = 한 컷 = 한 대사 |
| 자막 | 화면 약 70% 높이, **굵은 흰색 한글 + 검은 외곽선**, 아래에 작은 **영어 자막** | 동일 레이아웃 + 화면 밖 화자는 노란 글씨 + 이름표 |
| 브랜딩 | 하단 중앙 반투명 워터마크 | `watermark` |
| 스토리 | 당당한 선배 → 실수/혼남 → "흔한 일입니다" 반복 개그 → 마무리 멘트 | Claude 작가 프롬프트에 "진지 → 강아지 본능 반전 → 펀치라인" 구조 명시 |
| 설명란 | 제3자 시점 3줄 ("초롱 씨는 세 번째 비행이래…") | `description` 동일 톤 |

## 1. 전체 흐름

```
 주제 한 줄 ─┐
 배추 사진 ──┼─▶ ① 대본 작성 (Claude)  ──▶ episode.yaml  ◀── 사람이 검수/수정
 캐릭터 시트 ┘                                  │
                                               ├─▶ ② 프롬프트 팩 ─▶ (이미지 AI) 컷별 키프레임 ─▶ (영상 AI) i2v 컷 ─┐
                                               │                                                     shots/scene_XX.*
                                               ├─▶ ③ 음성 합성 (edge-tts / ElevenLabs 등) ─┐                 │
                                               │                                           ▼                 ▼
                                               └────────────────────────────────▶ ④ 합성·렌더링 (타임라인·자막·BGM·효과음)
                                                                                         │
                                                                                         ▼
                                                                ⑤ 산출물: MP4 · SRT · 썸네일 · 업로드 메타데이터
```

핵심 설계 원칙

1. **대본(YAML)이 유일한 원본.** 대사·자막·샷 크기·카메라 무빙·표정·효과음이 모두 한 파일에 있고, 나머지 단계는 전부 이 파일에서 파생된다. 대사 한 줄을 고치면 다시 렌더하면 끝.
2. **AI 생성물은 "끼워 넣는" 구조.** `shots/scene_XX.mp4|png`가 있으면 그걸 쓰고, 없으면 원본 사진에 가상 카메라 무빙을 입힌 컷으로 대체한다. → AI 결과물이 하나도 없어도 끝까지 렌더되고, 비싼 생성은 필요한 컷만 골라서 돌릴 수 있다.
3. **캐릭터 일관성은 캐릭터 시트로.** `characters/baechu/character.yaml`의 외형 묘사 + 레퍼런스 사진을 모든 이미지 프롬프트에 똑같이 넣는다.
4. **타이밍은 음성 길이가 결정.** 컷 길이 = 대사 음성 길이 + 여유(`hold`). 영상 AI 결과물이 짧으면 마지막 프레임을 유지하고 길면 자른다.

## 2. 단계별 상세

### ① 대본 작성 — `python -m baechu_shorts write "주제"`
- Claude(`claude-opus-5`)에 배추 사진 + 캐릭터 설정 + 포맷 규칙을 주고, **구조화 출력(Pydantic 스키마)**으로 episode.yaml을 받는다.
- 규칙: 30~45초, 10~14컷, 1컷 훅, 진지 → 본능 반전, 반전 컷은 `punch`/`shake` + 클로즈업, 마지막 컷 펀치라인.
- 생성된 YAML은 사람이 읽고 고치는 것을 전제로 한다 (웃음 포인트는 검수 한 번이 제일 효과적).

### ② 비주얼 — `python -m baechu_shorts prompts episode.yaml`
`output/prompts.md|json`에 도구 중립적인 프롬프트가 나온다.

| 단계 | 추천 도구 (택1) | 입력 | 출력 |
|---|---|---|---|
| 캐릭터 시트 | Gemini 2.5 Flash Image(나노바나나), GPT 이미지, Flux Kontext | 배추 사진 + `character_sheet.prompt` | 정장 입은 배추 정면/측면 시트 |
| 컷 키프레임 | 위와 동일 (레퍼런스 이미지 편집 모드) | 캐릭터 시트 + `image_prompt` | `shots/scene_XX.png` |
| 컷 영상 | Kling, Veo 3, Hailuo, Runway (i2v) | 키프레임 + `video_prompt` | `shots/scene_XX.mp4` |
| 립싱크 | Veo 3(대사 직접 생성) 또는 TTS 음성 + 립싱크 도구(Hedra, Kling Lip Sync 등) | 음성 + 컷 영상 | 입 모양 맞춘 컷 |

**자동 생성** — `python -m baechu_shorts generate episode.yaml` (환경 변수 `FAL_KEY`)
1. `fal-ai/nano-banana/edit`: 배추 사진 → `shots/character_sheet.png` (정장 입은 배추)
2. 같은 모델: 사진 + 캐릭터 시트 → 컷별 `shots/scene_XX.png` (9:16)
3. `fal-ai/kling-video/v2.5-turbo/pro/image-to-video`: 키프레임 → `shots/scene_XX.mp4` (5초, 렌더러가 대사 길이에 맞춰 자름)
- 이미 있는 파일은 건너뛴다 → 마음에 안 드는 컷 파일만 지우고 다시 실행. `--stage images`로 키프레임만 먼저 뽑아 검수 후 `--stage videos` 권장.
- 한 컷이 실패해도 나머지는 계속 진행, 실패 컷은 사진 폴백으로 렌더된다.

- 화면 밖 화자(면접관)의 컷은 배추가 "듣는" 리액션 컷으로 만든다. 레퍼런스처럼 상대역도 AI 인물로 보여 주려면 voices에서 `offscreen: false`로 두고 해당 컷 키프레임에 인물을 그리면 된다.
- 프롬프트에 "하단 1/3 비우기"를 넣어 자막 공간을 확보한다.

### ③ 음성
- 기본: `edge-tts` (무료, 한국어 `ko-KR-SunHiNeural`/`ko-KR-InJoonNeural`). 배추는 피치를 올려 귀엽게.
- 품질 업그레이드: ElevenLabs / Typecast / Supertone으로 교체 (`tts.py`의 `synthesize`만 바꾸면 됨).
- 합성 결과는 `~/.cache/baechu-shorts/tts`에 캐시 → 대사 안 바뀐 컷은 재합성하지 않음.

### ④ 합성·렌더링 — `python -m baechu_shorts render episode.yaml`
- **타임라인**: 컷마다 `LEAD(0.1s) + 음성 길이 + hold`.
- **가상 카메라(로컬 폴백)**: 사진을 2배 업스케일+샤픈 후, 샷(`wide/medium/close/extreme_close`)별 줌과 초점(몸 ↔ 얼굴), 무빙(`push_in/pull_out/pan/punch/shake/static`)으로 크롭 박스를 애니메이션. 클로즈업에서는 얼굴을 화면 위 40% 지점에 둬 자막과 안 겹치게. 배추가 말하는 동안에는 음량(RMS)에 맞춰 살짝 들썩여 "말하는 느낌"을 준다.
- **오버레이**: 상단 고정 제목, 한/영 자막(외곽선), 화면 밖 화자 이름표, 워터마크, `stamp`(합격 도장 애니메이션), 펀치인 순간 플래시.
- **오디오**: 대사 + 합성 BGM(저작권 무관 우쿨렐레풍 루프, 또는 파일 지정) + 효과음(`pop`) + 대사 구간 BGM 더킹 + 피크 노멀라이즈.
- **인코딩**: 프레임을 ffmpeg로 파이프 → H.264 CRF 21 / AAC 160k / faststart.

### ⑤ 산출물 (`<에피소드>/output/`)
`<slug>.mp4`, `<slug>.srt`(업로드용 자막), `thumbnail.jpg`(도장 컷), `metadata.md`(제목/설명/해시태그), `prompts.md|json`.

## 3. 운영 (시리즈화)

- **에피소드 폴더 = 한 편.** `examples/<slug>/episode.yaml` + `shots/` + `output/`.
- **시리즈 아이디어**: 면접 → 첫 출근 → 회식 → 야근 → 연봉협상 → 퇴사 (같은 세계관 반복이 채널 인지도에 유리). 레퍼런스처럼 "흔한 일입니다" 같은 반복 대사를 시리즈 밈으로.
- **비용 감각**: 대본 1회 호출 + 이미지 13장 + i2v 13컷. i2v가 대부분의 비용이므로 반전/펀치라인 컷만 AI 영상으로 만들고 나머지는 키프레임+카메라 무빙으로 두는 **하이브리드**가 가성비가 좋다 (구조상 컷 단위로 섞을 수 있음).
- **업로드 전 체크**: 자막 오탈자, 첫 1초 훅, 음량(대사 명료), AI 생성물 표기(YouTube "변경되거나 합성된 콘텐츠" 체크).

## 4. 확장 포인트
| 하고 싶은 것 | 고칠 곳 |
|---|---|
| TTS 엔진 교체 | `baechu_shorts/tts.py` `synthesize()` |
| 무료 생성 (HF Spaces) | `generate --provider hf` + `HF_TOKEN` (무료 계정 하루 GPU 5분, 다음 날 이어서 실행) |
| 이미지/영상 모델 교체 | `generate.py`의 `IMAGE_MODEL`/`VIDEO_MODEL` 또는 `--video-model` |
| 립싱크 | 컷 영상 + TTS 음성 → 립싱크 모델 결과를 `shots/scene_XX.mp4`로 덮어쓰기 |
| 자막 스타일 | `compose.py` `Overlays` |
| 새 카메라 무빙 | `episode.MOVES` + `StillSource.frame()` |
| 새 효과음 | `audio.SFX` |
