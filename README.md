# 배추 쇼츠 🥬🐶

토이푸들 **배추**가 사람처럼 일하고 말하는 의인화 상황극 쇼츠를 만드는 파이프라인.
대본(YAML) 한 파일로 음성·컷·자막·BGM을 합쳐 1080×1920 MP4를 만든다.

- 설계 문서: [`docs/PIPELINE.md`](docs/PIPELINE.md)
- 실제 예시: [`examples/interview/`](examples/interview/) — 「면접 보는 배추 😎」 34초
  - 결과 영상: `examples/interview/output/interview.mp4`
  - AI 생성용 프롬프트 팩: `examples/interview/output/prompts.md`

## 빠른 시작

```bash
pip install -r requirements.txt

# 1) (선택) Claude로 대본 생성 — ANTHROPIC_API_KEY 필요
python -m baechu_shorts write "배추의 첫 편의점 알바" -o examples/cvs/episode.yaml

# 2) AI 이미지/영상 도구에 넣을 프롬프트 팩
python -m baechu_shorts prompts examples/interview/episode.yaml

# 3) 렌더링 → examples/interview/output/
python -m baechu_shorts render examples/interview/episode.yaml
```

AI로 만든 컷을 `examples/<에피소드>/shots/scene_03.mp4`(또는 `.png`)로 저장하면 다음 렌더부터 그 컷만 교체된다.
아무것도 없으면 배추 원본 사진에 카메라 무빙(줌·펀치인·흔들기)을 입혀 컷을 만든다.

## 구조

```
characters/baechu/      캐릭터 시트(외형·성격·목소리) + 레퍼런스 사진
examples/<slug>/        에피소드: episode.yaml, shots/(AI 결과물), output/
baechu_shorts/
  episode.py            대본 로딩/검증
  writer.py             ① Claude 대본 작성
  prompts.py            ② 이미지·i2v 프롬프트 팩
  tts.py                ③ 음성 합성 (edge-tts)
  audio.py              BGM 합성·효과음·더킹·믹스
  compose.py            ④ 가상 카메라·자막·오버레이·인코딩
```

환경 변수: `FFMPEG`(ffmpeg 경로, 없으면 pip 번들 사용), `BAECHU_FONT`(폰트 지정, 없으면 Noto Sans KR 자동 다운로드), `SSL_CERT_FILE`(프록시 CA).
