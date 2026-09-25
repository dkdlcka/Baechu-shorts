"""외부 도구(ffmpeg, 폰트) 경로 해결."""
from __future__ import annotations

import os
import shutil
import urllib.request
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

CACHE_DIR = Path(os.environ.get("BAECHU_CACHE", Path.home() / ".cache" / "baechu-shorts"))
FONT_URL = "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/notosanskr/NotoSansKR%5Bwght%5D.ttf"


@lru_cache(maxsize=1)
def ffmpeg() -> str:
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg  # 시스템 ffmpeg가 없으면 pip 번들 바이너리 사용

    return imageio_ffmpeg.get_ffmpeg_exe()


@lru_cache(maxsize=1)
def korean_font_path() -> Path:
    """Noto Sans KR(가변 폰트, OFL). BAECHU_FONT로 다른 폰트를 지정할 수 있다."""
    if os.environ.get("BAECHU_FONT"):
        return Path(os.environ["BAECHU_FONT"])
    path = CACHE_DIR / "fonts" / "NotoSansKR-wght.ttf"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"[font] 한글 폰트 다운로드: {FONT_URL}")
        urllib.request.urlretrieve(FONT_URL, path)
    return path


@lru_cache(maxsize=None)
def font(size: int, weight: int = 900) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(korean_font_path()), size)
    try:
        f.set_variation_by_axes([weight])
    except OSError:
        pass  # 가변 폰트가 아니면 무시
    return f
