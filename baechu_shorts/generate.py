"""② AI 생성 단계: 컷 키프레임 → 컷 영상 자동 생성. 두 가지 제공자:

  --provider hf  (무료) Hugging Face ZeroGPU Spaces, HF_TOKEN 필요(무료 계정 하루 GPU 5분)
      이미지: black-forest-labs/FLUX.1-Kontext-Dev  (배추 사진을 직접 편집 → 같은 강아지 유지)
      영상:   zerogpu-aoti/wan2-2-fp8da-aoti-faster (Wan 2.2 이미지→영상)
  --provider fal (유료, 고품질) FAL_KEY 필요
      이미지: fal-ai/nano-banana/edit, 영상: fal-ai/kling-video/v2.5-turbo/pro/image-to-video

결과는 <에피소드>/shots/ 에 저장되고, 이미 있는 파일은 건너뛴다(마음에 안 드는 컷만 지우고 다시 실행).
렌더러는 shots/scene_XX.mp4 → .png → 원본 사진 순으로 컷 소스를 고른다.
"""
from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from .episode import Episode
from .prompts import build

QUEUE = os.environ.get("FAL_QUEUE_URL", "https://queue.fal.run")
IMAGE_MODEL = "fal-ai/nano-banana/edit"
VIDEO_MODEL = "fal-ai/kling-video/v2.5-turbo/pro/image-to-video"
IDENTITY = ("Use the exact same dog as in the reference images: identical fur colors, face, eyes and ears. "
            "Photorealistic, like a real photo of this dog.")


def _key() -> str:
    key = os.environ.get("FAL_KEY")
    if not key:
        raise SystemExit("FAL_KEY 환경 변수가 없습니다. fal.ai 대시보드에서 API 키를 만들어 환경 변수로 설정하세요.")
    return key


def _http(method: str, url: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Key {_key()}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def run(model: str, args: dict, label: str, poll: float = 3.0, timeout: float = 900) -> dict:
    """fal 큐에 작업을 넣고 끝날 때까지 기다린 뒤 결과 JSON을 돌려준다."""
    job = _http("POST", f"{QUEUE}/{model}", args)
    status_url = job.get("status_url") or f"{QUEUE}/{model}/requests/{job['request_id']}/status"
    result_url = job.get("response_url") or f"{QUEUE}/{model}/requests/{job['request_id']}"
    start = time.time()
    while True:
        st = _http("GET", status_url)
        if st.get("status") == "COMPLETED":
            if st.get("error"):
                raise RuntimeError(f"{label}: {st['error']}")
            return _http("GET", result_url)
        if time.time() - start > timeout:
            raise TimeoutError(f"{label}: {timeout:.0f}초 안에 끝나지 않음")
        time.sleep(poll)


def data_uri(path: Path, max_side: int = 1536) -> str:
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=300) as r:
        tmp.write_bytes(r.read())
    tmp.rename(dest)
    return dest


def _image(prompt: str, refs: list[Path], dest: Path, label: str, aspect: str = "9:16") -> Path:
    out = run(IMAGE_MODEL, {"prompt": prompt, "image_urls": [data_uri(p) for p in refs],
                            "num_images": 1, "aspect_ratio": aspect, "output_format": "png"}, label)
    return download(out["images"][0]["url"], dest)


def _video(prompt: str, frame: Path, dest: Path, label: str, model: str) -> Path:
    out = run(model, {"prompt": prompt, "image_url": data_uri(frame), "duration": "5",
                      "negative_prompt": "blur, distort, low quality, extra legs, deformed face, text"}, label)
    return download(out["video"]["url"], dest)


# ---------------------------------------------------------------- Hugging Face (무료)
HF_IMAGE_SPACE = "black-forest-labs/FLUX.1-Kontext-Dev"
HF_VIDEO_SPACE = "zerogpu-aoti/wan2-2-fp8da-aoti-faster"
HF_NEGATIVE = "static, blurry, low quality, subtitles, text, deformed face, extra legs, extra paws, human hands"


def _hf_client(space: str):
    from gradio_client import Client

    token = os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit("HF_TOKEN 환경 변수가 없습니다. huggingface.co/settings/tokens 에서 무료 토큰(Read)을 만드세요.")
    try:
        return Client(space, token=token, verbose=False)
    except TypeError:  # 구버전 gradio_client
        return Client(space, hf_token=token, verbose=False)


def _hf_copy(result, dest: Path) -> Path:
    src = result.get("path") if isinstance(result, dict) else result
    if isinstance(src, dict):  # Video 출력은 {"video": path} 형태일 수 있음
        src = src.get("video") or src.get("path")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.suffix == ".png":
        Image.open(src).convert("RGB").save(dest)
    else:
        dest.write_bytes(Path(src).read_bytes())
    return dest


def generate_hf(ep: Episode, stage: str, scenes: list[int] | None) -> None:
    """무료 경로. ZeroGPU 할당량을 아끼려고 순차 실행하고, 할당량이 바닥나면 멈춘다(내일 이어서 실행)."""
    from gradio_client import handle_file

    pack = build(ep)
    ref = ep.character.ref_images[0]
    todo = [i for i in range(len(pack["scenes"])) if scenes is None or i in scenes]
    img_client = vid_client = None
    for i in todo:
        sc = pack["scenes"][i]
        png, mp4 = ep.dir / sc["file"], ep.dir / sc["video_file"]
        try:
            if stage in ("all", "images") and not png.exists():
                img_client = img_client or _hf_client(HF_IMAGE_SPACE)
                prompt = (f"Keep this exact dog (same fur colors, face, eyes and ears). Make it {ep.wardrobe}. "
                          f"{ep.scenes[i].expression}. Background: {ep.setting}. "
                          f"Photorealistic photo.")
                res, _ = img_client.predict(handle_file(str(ref)), prompt, 0, True, 2.5, 28, api_name="/infer")
                _hf_copy(res, png)
            if stage in ("all", "videos") and png.exists() and not mp4.exists():
                vid_client = vid_client or _hf_client(HF_VIDEO_SPACE)
                res, _ = vid_client.predict(handle_file(str(png)), sc["video_prompt"], 6, HF_NEGATIVE, 3.5,
                                            1, 1, 42, True, api_name="/generate_video")
                _hf_copy(res, mp4)
            print(f"[generate:hf] scene_{i:02d}: {'mp4' if mp4.exists() else 'png' if png.exists() else '-'}")
        except Exception as e:
            msg = str(e)
            print(f"[generate:hf] scene_{i:02d} 실패: {msg}")
            if "quota" in msg.lower() or "limit" in msg.lower():
                print("[generate:hf] 오늘 GPU 할당량 소진 → 24시간 뒤 같은 명령을 다시 실행하면 남은 컷부터 이어서 만듭니다.")
                return


# ---------------------------------------------------------------- fal.ai (유료)
def generate(ep: Episode, stage: str = "all", scenes: list[int] | None = None,
             video_model: str = VIDEO_MODEL, workers: int = 4) -> None:
    pack = build(ep)
    shots = ep.dir / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    todo = [i for i in range(len(pack["scenes"])) if scenes is None or i in scenes]
    refs = list(ep.character.ref_images)

    # 0) 캐릭터 시트: 모든 컷이 같은 의상/외형을 공유하도록 먼저 한 장 만든다
    sheet = shots / "character_sheet.png"
    if stage in ("all", "images") and not sheet.exists():
        print("[generate] 캐릭터 시트 생성")
        _image(f"{pack['character_sheet']['prompt']} {IDENTITY}", refs, sheet, "character_sheet", aspect="3:2")
    identity_refs = refs + ([sheet] if sheet.exists() else [])

    def one(i: int) -> str:
        sc = pack["scenes"][i]
        png, mp4 = ep.dir / sc["file"], ep.dir / sc["video_file"]
        if stage in ("all", "images") and not png.exists():
            _image(f"{sc['image_prompt']} {IDENTITY}", identity_refs, png, f"scene_{i:02d} image")
        if stage in ("all", "videos") and png.exists() and not mp4.exists():
            _video(sc["video_prompt"], png, mp4, f"scene_{i:02d} video", video_model)
        return f"scene_{i:02d}: {'mp4' if mp4.exists() else 'png' if png.exists() else '-'}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {i: pool.submit(one, i) for i in todo}
        for i, f in futures.items():
            try:
                print(f"[generate] {f.result()}")
            except Exception as e:  # 한 컷이 실패해도 나머지는 계속
                print(f"[generate] scene_{i:02d} 실패: {e}")
