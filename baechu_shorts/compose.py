"""컷 영상 + 자막 + 오디오 → 1080x1920 MP4.

각 컷의 화면 소스는 우선순위대로 결정된다:
  1. <에피소드>/shots/scene_XX.mp4   ← AI 이미지→영상(i2v) 결과물
  2. <에피소드>/shots/scene_XX.png|jpg ← AI 생성 키프레임 (카메라 무빙만 입힘)
  3. 캐릭터 레퍼런스 사진            ← 로컬 폴백: 크롭/줌/펀치인/흔들기로 컷을 만든다
따라서 AI 결과물이 하나도 없어도 끝까지 렌더링되고, 결과물이 생기는 컷부터 자연스럽게 교체된다.
"""
from __future__ import annotations

import math
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFilter

from . import audio as audio_mod
from .episode import Episode, Scene, keyframe_path
from .tools import ffmpeg, font
from .tts import SR, synthesize

W, H, FPS = 1080, 1920, 30
LEAD = 0.10  # 컷 시작 후 대사가 나오기까지 간격(초)

SHOT_ZOOM = {"wide": 1.0, "medium": 1.3, "close": 1.6, "extreme_close": 2.0}
FACE_Y = 0.40  # 얼굴을 화면 위쪽 40% 지점에 둬서 하단 자막과 겹치지 않게
KO_COLOR = {"on": (255, 255, 255), "off": (255, 230, 109)}


# ---------------------------------------------------------------- 타임라인
@dataclass
class Cut:
    scene: Scene
    start: float
    duration: float
    voice: np.ndarray

    @property
    def end(self) -> float:
        return self.start + self.duration


def build_timeline(ep: Episode) -> list[Cut]:
    cuts, t = [], 0.0
    for s in ep.scenes:
        v = synthesize(s.ko, ep.voices[s.speaker])
        dur = LEAD + len(v) / SR + s.hold
        cuts.append(Cut(s, t, dur, v))
        print(f"  [{s.index:02d}] {t:5.2f}s +{dur:4.2f}s  {ep.voices[s.speaker].label}: {s.ko}")
        t += dur
    return cuts


def build_audio(ep: Episode, cuts: list[Cut]) -> np.ndarray:
    total = int(math.ceil(cuts[-1].end * SR)) + SR // 2
    voice = np.zeros(total, dtype=np.float32)
    sfx = np.zeros(total, dtype=np.float32)
    for c in cuts:
        a = int((c.start + LEAD) * SR)
        voice[a:a + len(c.voice)] += c.voice
        if c.scene.sfx in audio_mod.SFX:
            s = audio_mod.SFX[c.scene.sfx]()
            b = int(c.start * SR)
            sfx[b:b + len(s)] += s[: total - b]
    cfg = ep.audio or {}
    return audio_mod.mix(voice, sfx, cfg.get("bgm", "synth"), float(cfg.get("bgm_volume", 0.12)), ep.dir)


# ---------------------------------------------------------------- 텍스트 그리기
def _wrap(draw: ImageDraw.ImageDraw, text: str, f, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split(" "):
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=f) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + [cur]


def text_patch(text: str, size: int, fill, stroke: int, weight: int = 900, max_w: int = 960,
               line_gap: int = 8) -> Image.Image:
    """외곽선 있는 여러 줄 텍스트를 투명 배경 RGBA 패치로."""
    f = font(size, weight)
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    lines = _wrap(tmp, text, f, max_w)
    asc, desc = f.getmetrics()
    lh = asc + desc
    width = int(max(tmp.textlength(l, font=f) for l in lines)) + stroke * 2 + 8
    height = lh * len(lines) + line_gap * (len(lines) - 1) + stroke * 2 + 8
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i, l in enumerate(lines):
        x = (width - d.textlength(l, font=f)) / 2
        y = stroke + 4 + i * (lh + line_gap)
        d.text((x, y), l, font=f, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
    return im


def pill_patch(text: str, size: int = 34, bg=(255, 230, 109), fg=(20, 20, 20)) -> Image.Image:
    f = font(size, 800)
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    tw = int(tmp.textlength(text, font=f))
    asc, desc = f.getmetrics()
    im = Image.new("RGBA", (tw + 36, asc + desc + 14), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, im.width - 1, im.height - 1], radius=im.height // 2, fill=bg)
    d.text((18, 6), text, font=f, fill=fg)
    return im


def stamp_patch(text: str) -> Image.Image:
    f = font(150, 900)
    red = (230, 40, 45, 235)
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    tw = int(tmp.textlength(text, font=f))
    asc, desc = f.getmetrics()
    im = Image.new("RGBA", (tw + 110, asc + desc + 70), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([8, 8, im.width - 9, im.height - 9], radius=34, outline=red, width=14)
    d.text((55, 22), text, font=f, fill=red)
    return im.rotate(12, resample=Image.BICUBIC, expand=True)


def gradient(h: int, top_alpha: int, bottom_alpha: int) -> Image.Image:
    a = np.linspace(top_alpha, bottom_alpha, h, dtype=np.float32)[:, None].repeat(W, 1)
    im = Image.new("RGBA", (W, h), (0, 0, 0, 0))
    im.putalpha(Image.fromarray(a.astype(np.uint8)))
    return im


class Overlays:
    """컷마다 바뀌지 않는 오버레이는 미리 한 번만 그린다."""

    def __init__(self, ep: Episode):
        self.ep = ep
        self.top_shade = gradient(430, 150, 0)
        self.bottom_shade = gradient(700, 0, 140)
        self.title = text_patch(ep.top_title, 70, (255, 255, 255), 9, max_w=980) if ep.top_title else None
        self.mark = text_patch(ep.watermark, 32, (255, 255, 255, 150), 0, weight=700) if ep.watermark else None
        self._subs: dict[int, list[tuple[Image.Image, int]]] = {}
        self._stamps: dict[str, Image.Image] = {}

    def subtitle(self, s: Scene) -> list[tuple[Image.Image, int]]:
        if s.index not in self._subs:
            v = self.ep.voices[s.speaker]
            ko = text_patch(s.ko, 68, KO_COLOR["off" if v.offscreen else "on"], 8, max_w=940)
            items, y = [], 1330
            if v.offscreen:
                pill = pill_patch(v.label)
                items.append((pill, y - pill.height - 12))
            items.append((ko, y))
            y += ko.height + 4
            if s.en:
                items.append((text_patch(s.en, 38, (255, 255, 255), 5, weight=700, max_w=940), y))
            self._subs[s.index] = items
        return self._subs[s.index]

    def stamp(self, text: str) -> Image.Image:
        if text not in self._stamps:
            self._stamps[text] = stamp_patch(text)
        return self._stamps[text]

    def draw(self, frame: Image.Image, cut: Cut, t_local: float) -> None:
        def paste(p: Image.Image, x: int, y: int) -> None:
            frame.paste(p, (x, y), p)

        paste(self.top_shade, 0, 0)
        paste(self.bottom_shade, 0, H - self.bottom_shade.height)
        if self.title:
            paste(self.title, (W - self.title.width) // 2, 190)
        for p, y in self.subtitle(cut.scene):
            paste(p, (W - p.width) // 2, y)
        if self.mark:
            paste(self.mark, (W - self.mark.width) // 2, 1790)
        if cut.scene.stamp:
            k = min(1.0, t_local / 0.22)
            scale = 1 + 1.2 * (1 - k) ** 2  # 쾅 하고 찍히는 느낌
            st = self.stamp(cut.scene.stamp)
            st = st.resize((int(st.width * scale), int(st.height * scale)), Image.BILINEAR)
            if k < 1:
                st.putalpha(st.getchannel("A").point(lambda a: int(a * k)))
            paste(st, (W - st.width) // 2, 1040 - st.height // 2)


# ---------------------------------------------------------------- 화면 소스
def _ease(p: float) -> float:
    return p * p * (3 - 2 * p)


class StillSource:
    """정지 이미지 + 가상 카메라(크롭 박스 애니메이션)."""

    UPSCALE = 2  # 저해상도 사진도 클로즈업에서 덜 뭉개지도록 미리 업스케일+샤픈

    def __init__(self, path: Path, face_box, body_center, zoom_scale: float = 1.0, mouth=None):
        self.zoom_scale = zoom_scale
        src = Image.open(path).convert("RGB")
        self.img = src.resize((src.width * self.UPSCALE, src.height * self.UPSCALE), Image.LANCZOS)
        self.img = self.img.filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=2))
        self._setup(self.img.size, face_box, body_center)
        self.jaw = Jaw(self.img, mouth) if mouth else None

    def _setup(self, size, face_box, body_center):
        self.w, self.h = size
        fx0, fy0, fx1, fy1 = face_box
        self.face = ((fx0 + fx1) / 2 * self.w, (fy0 + fy1) / 2 * self.h)
        self.body = (body_center[0] * self.w, body_center[1] * self.h)
        # 9:16로 꽉 채우는 기본 크롭 크기
        if self.w / self.h > W / H:
            self.base = (self.h * W / H, self.h)
        else:
            self.base = (self.w, self.w * H / W)

    def frame(self, cut: Cut, t: float, env: float, talking: bool, seed: int) -> Image.Image:
        img = self.img
        if self.jaw and cut.scene.lipsync:
            img = self.jaw.apply(env)
        return img.resize((W, H), Image.BILINEAR, box=self.camera_box(cut, t, env, talking, seed))

    def camera_box(self, cut: Cut, t: float, env: float, talking: bool, seed: int):
        s = cut.scene
        p = _ease(min(1.0, t / max(cut.duration, 1e-3)))
        zoom = 1 + (SHOT_ZOOM[s.shot] - 1) * self.zoom_scale
        # 샷이 좁을수록 얼굴 쪽으로 초점 이동
        k = {"wide": 0.0, "medium": 0.55, "close": 1.0, "extreme_close": 1.0}[s.shot]
        cx = self.body[0] + (self.face[0] - self.body[0]) * k
        cy = self.body[1] + (self.face[1] - self.body[1]) * k
        dx = dy = 0.0

        if s.move == "push_in":
            zoom *= 1 + 0.12 * p
        elif s.move == "pull_out":
            zoom *= 1.14 - 0.14 * p
        elif s.move in ("pan_left", "pan_right"):
            zoom *= 1.08
            dx = (0.5 - p if s.move == "pan_left" else p - 0.5) * 0.10 * self.base[0]
        elif s.move == "punch":  # 순간 줌인(밈 편집의 핵심)
            zoom *= 1 + 0.22 * (1 - (1 - min(1.0, t / 0.16)) ** 3) + 0.03 * p
        elif s.move == "shake":
            amp = 0.012 + 0.03 * max(0.0, 1 - t / 0.4)
            rng = np.random.default_rng(seed)
            dx, dy = rng.uniform(-1, 1, 2) * amp * self.base[0]
            zoom *= 1.05
        else:  # static: 아주 약한 드리프트로 정지화면 느낌 제거
            zoom *= 1 + 0.025 * p

        if talking:  # 말할 때 음량에 맞춰 살짝 들썩
            zoom *= 1 + 0.018 * env
            dy -= 0.012 * env * self.base[1]

        cw, ch = self.base[0] / zoom, self.base[1] / zoom
        cy += (0.5 - FACE_Y) * ch * k
        x0 = min(max(cx + dx - cw / 2, 0), self.w - cw)
        y0 = min(max(cy + dy - ch / 2, 0), self.h - ch)
        return (x0, y0, x0 + cw, y0 + ch)


class Jaw:
    """정지 이미지 립싱크: 입술 선을 기준으로 아래턱은 크게, 윗입술은 조금 벌리고
    벌어진 틈은 입안(위는 어둡고 아래는 혀 색) 그라데이션으로 채운다."""

    DARK = np.array([45, 12, 18], np.float32)
    TONGUE = np.array([170, 70, 85], np.float32)
    UP = 0.25  # 벌어짐 중 윗입술이 올라가는 비율

    def __init__(self, img: Image.Image, mouth):
        mx, my, mw = mouth
        W_, H_ = img.size
        self.base = np.asarray(img).astype(np.float32)
        self.H = H_
        self.mx, self.my, self.mw = mx * W_, my * H_, mw * W_
        self.x0 = int(max(0, self.mx - 1.2 * self.mw))
        self.x1 = int(min(W_, self.mx + 1.2 * self.mw))
        self.jaw_h, self.lip_h = 1.6 * self.mw, 0.5 * self.mw
        self.y0 = int(max(0, self.my - self.lip_h))
        self.y1 = int(min(H_, self.my + self.jaw_h + 2))
        xs = np.arange(self.x0, self.x1, dtype=np.float32)
        ys = np.arange(self.y0, self.y1, dtype=np.float32)
        gx = np.exp(-(((xs - self.mx) / (0.38 * self.mw)) ** 2))[None, :]  # 가운데가 가장 크게 벌어짐
        below = ys[:, None] >= self.my
        fall = np.where(below, np.clip(1 - (ys[:, None] - self.my) / self.jaw_h, 0, 1),
                        np.clip(1 - (self.my - ys[:, None]) / self.lip_h, 0, 1))
        self.shape = gx * fall            # 위치별 이동 비율
        self.below = below
        self.yy = ys[:, None].repeat(len(xs), 1)
        self.xx = np.arange(len(xs))[None, :].repeat(len(ys), 0)
        self._cache: dict[int, Image.Image] = {}

    def apply(self, env: float) -> Image.Image:
        opening = float(np.clip((env - 0.10) / 0.55, 0, 1))
        level = int(round(opening * 12))  # 13단계로 양자화해서 캐시
        if level not in self._cache:
            if level == 0:
                out = self.base
            else:
                a = 0.36 * self.mw * level / 12
                d_dn = (1 - self.UP) * a * self.shape
                d_up = self.UP * a * self.shape
                sy = np.where(self.below, self.yy - d_dn, self.yy + d_up)
                src = self.base[:, self.x0:self.x1]
                lo = np.clip(np.floor(sy).astype(int), 0, self.H - 1)
                hi = np.clip(lo + 1, 0, self.H - 1)
                f = (sy - np.floor(sy))[..., None]
                warped = src[lo, self.xx] * (1 - f) + src[hi, self.xx] * f
                # 입술 선을 넘어서 끌어온 픽셀 = 벌어진 입 안
                cross = np.where(self.below, self.my - sy, sy - self.my)
                gap = (d_dn + d_up)[..., None]
                inside = np.clip(cross[..., None] / 1.5, 0, 1) * np.clip(gap - 1.0, 0, 1)
                # 아래쪽(혀)일수록 분홍, 위쪽일수록 어둡게
                depth = np.where(self.below, np.clip((self.yy - self.my) / np.maximum(d_dn, 1e-3), 0, 1), 0.0)[..., None]
                color = self.DARK * (1 - depth * 0.8) + self.TONGUE * (depth * 0.8)
                warped = warped * (1 - inside) + color * inside
                out = self.base.copy()
                out[self.y0:self.y1, self.x0:self.x1] = warped
            self._cache[level] = Image.fromarray(out.astype(np.uint8))
        return self._cache[level]


class VideoSource(StillSource):
    """AI가 만든 컷 영상. 정지 컷과 같은 가상 카메라(샷/무빙)를 입히고,
    대사보다 짧으면 앞뒤로 왕복 재생(핑퐁)해서 멈춘 화면이 생기지 않게 한다."""

    def __init__(self, path: Path, face_box, body_center, zoom_scale: float = 1.0):
        import imageio_ffmpeg

        self.zoom_scale = zoom_scale
        gen = imageio_ffmpeg.read_frames(str(path), output_params=["-vf", f"fps={FPS}"])
        meta = next(gen)
        w, h = meta["size"]
        self.frames = [np.frombuffer(f, np.uint8).reshape(h, w, 3) for f in gen]
        self._setup((w, h), face_box, body_center)

    def frame(self, cut: Cut, t: float, env: float, talking: bool, seed: int) -> Image.Image:
        n = len(self.frames)
        period = max(1, 2 * n - 2)
        i = int(t * FPS) % period
        i = i if i < n else period - i
        img = Image.fromarray(self.frames[i])
        # 영상 자체가 움직이므로 음량 들썩임은 끔
        out = img.resize((W, H), Image.BICUBIC, box=self.camera_box(cut, t, 0.0, False, seed))
        return out.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))


AI_FACE_BOX, AI_BODY = (0.3, 0.14, 0.7, 0.46), (0.5, 0.55)  # AI 키프레임: 캐릭터가 가운데 있다고 가정
AI_ZOOM = 0.6  # AI 키프레임은 머리가 크게 나와서 클로즈업을 덜 조인다


def pick_source(ep: Episode, s: Scene, cache: dict):
    shots = ep.dir / "shots"
    for ext in (".mp4", ".mov", ".webm"):
        p = shots / f"scene_{s.index:02d}{ext}"
        if p.exists():
            return VideoSource(p, AI_FACE_BOX, AI_BODY, AI_ZOOM)
    kf = keyframe_path(ep, s)
    if kf.exists():
        if kf not in cache:
            mouths_file = shots / "mouths.yaml"
            mouths = yaml.safe_load(mouths_file.read_text()) if mouths_file.exists() else {}
            cache[kf] = StillSource(kf, AI_FACE_BOX, AI_BODY, AI_ZOOM, mouth=(mouths or {}).get(kf.stem))
        return cache[kf]
    for ext in (".jpg", ".jpeg", ".webp"):
        p = shots / f"scene_{s.index:02d}{ext}"
        if p.exists():
            # AI 키프레임은 캐릭터가 대략 가운데 있다고 가정
            return StillSource(p, (0.3, 0.2, 0.7, 0.45), (0.5, 0.55))
    ref = ep.character.ref_images[0]
    if ref not in cache:
        cache[ref] = StillSource(ref, ep.character.face_box, ep.character.body_center)
    return cache[ref]


# ---------------------------------------------------------------- 출력물
def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_srt(ep: Episode, cuts: list[Cut], path: Path) -> None:
    rows = []
    for i, c in enumerate(cuts, 1):
        v = ep.voices[c.scene.speaker]
        who = f"({v.label}) " if v.offscreen else ""
        rows.append(f"{i}\n{_ts(c.start)} --> {_ts(c.end)}\n{who}{c.scene.ko}\n{c.scene.en}\n")
    path.write_text("\n".join(rows), encoding="utf-8")


def write_metadata(ep: Episode, cuts: list[Cut], path: Path) -> None:
    path.write_text(
        f"# {ep.title}\n\n"
        f"- 길이: {cuts[-1].end:.1f}s · {W}x{H} · {FPS}fps\n\n"
        f"## 업로드 제목\n{ep.title}\n\n"
        f"## 설명\n{ep.description.strip()}\n\n{' '.join(ep.hashtags)}\n",
        encoding="utf-8",
    )


def render(ep: Episode, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    print("[1/4] 음성 합성 + 타임라인")
    cuts = build_timeline(ep)
    total = cuts[-1].end
    n_frames = int(math.ceil(total * FPS))

    print("[2/4] 오디오 믹스 (대사 + BGM + 효과음)")
    mix = build_audio(ep, cuts)
    wav_path = out_dir / "audio.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes((np.clip(mix, -1, 1) * 32767).astype(np.int16).tobytes())
    voice_only = np.zeros_like(mix)
    for c in cuts:
        a = int((c.start + LEAD) * SR)
        voice_only[a:a + len(c.voice)] += c.voice[: len(voice_only) - a]
    env = audio_mod.envelope(voice_only, FPS, n_frames)

    print(f"[3/4] 프레임 합성 ({n_frames} frames) + 인코딩")
    slug = ep.path.parent.name
    mp4 = out_dir / f"{slug}.mp4"
    enc = subprocess.Popen(
        [ffmpeg(), "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-i", str(wav_path),
         "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(mp4)],
        stdin=subprocess.PIPE,
    )
    overlays, cache, sources = Overlays(ep), {}, {}
    thumb_frame = None
    ci = 0
    for fi in range(n_frames):
        t = fi / FPS
        while ci < len(cuts) - 1 and t >= cuts[ci].end:
            ci += 1
        cut = cuts[ci]
        s = cut.scene
        if s.index not in sources:
            sources[s.index] = pick_source(ep, s, cache)
        talking = not ep.voices[s.speaker].offscreen
        frame = sources[s.index].frame(cut, t - cut.start, float(env[fi]), talking, seed=fi)
        overlays.draw(frame, cut, t - cut.start)
        if t - cut.start < 2 / FPS and s.move == "punch":  # 펀치인 순간 짧은 플래시
            frame = Image.blend(frame, Image.new("RGB", frame.size, "white"), 0.35)
        enc.stdin.write(frame.tobytes())
        if s.stamp and thumb_frame is None and t - cut.start > 0.4:
            thumb_frame = frame.copy()
        if fi % (FPS * 5) == 0:
            print(f"    {t:5.1f}s / {total:.1f}s")
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError("ffmpeg 인코딩 실패")

    print("[4/4] 자막(SRT)·썸네일·메타데이터")
    write_srt(ep, cuts, out_dir / f"{slug}.srt")
    write_metadata(ep, cuts, out_dir / "metadata.md")
    (thumb_frame or frame).convert("RGB").save(out_dir / "thumbnail.jpg", quality=90)
    wav_path.unlink()
    print(f"완료 → {mp4}  ({total:.1f}s)")
    return mp4
