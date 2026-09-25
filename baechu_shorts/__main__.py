"""CLI.

  python -m baechu_shorts write   "주제" --character characters/baechu/character.yaml -o examples/x/episode.yaml
  python -m baechu_shorts prompts examples/interview/episode.yaml
  python -m baechu_shorts render  examples/interview/episode.yaml
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .episode import load_episode


def main() -> None:
    ap = argparse.ArgumentParser(prog="baechu_shorts")
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write", help="Claude로 대본 생성 (ANTHROPIC_API_KEY 필요)")
    w.add_argument("topic")
    w.add_argument("--character", type=Path, default=Path("characters/baechu/character.yaml"))
    w.add_argument("-o", "--out", type=Path, required=True)

    for name, help_ in (("prompts", "AI 이미지/영상 프롬프트 팩 생성"), ("render", "최종 MP4 렌더링")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("episode", type=Path)
        p.add_argument("--out-dir", type=Path, help="기본값: <에피소드 폴더>/output")

    a = ap.parse_args()
    if a.cmd == "write":
        from .writer import write_episode

        print(f"대본 저장 → {write_episode(a.topic, a.character, a.out)}")
        return

    ep = load_episode(a.episode)
    out_dir = a.out_dir or ep.dir / "output"
    if a.cmd == "prompts":
        from .prompts import write

        print(f"프롬프트 팩 → {write(ep, out_dir)}")
    else:
        from .compose import render

        render(ep, out_dir)


if __name__ == "__main__":
    main()
