"""
pull_live.py — 배포된 사이트의 최신 데이터를 로컬 dist/로 내려받는다
=====================================================================
**로컬에서 배포하기 전에 반드시 먼저 실행할 것.**

이 프로젝트의 데이터 파일(data.json, history.json 등)은 .gitignore에 있고,
매일 07:00 KST에 도는 자동화(CCR)는 커밋을 하지 않는다. 즉 **최신 데이터는
깃이 아니라 배포된 사이트에만 있다.** 그 상태에서 로컬 dist/를 그대로 올리면
자동화가 만든 하루치 결과가 통째로 날아간다 (실제로 한 번 그랬다).

history.json과 company_notes.json은 하루하루 쌓이는 파일이라 피해가 특히 크다.
되돌리려면 Netlify 배포 이력에서 이전 배포를 찾아야 한다.

받아온 파일이 로컬보다 **오래됐으면 덮어쓰지 않는다** — 방금 로컬에서 새로
만든 결과를 이 스크립트가 거꾸로 지워버리면 안 되기 때문이다.

실행:  python pull_live.py
"""

from __future__ import annotations
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

SITE = "https://today-watchlist-kr.netlify.app"
DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "card_news", "dist")

# 자동화가 매일 새로 쓰거나 쌓아가는 파일들
FILES = [
    "data.json", "radar.json", "quality.json",
    "history.json", "company_notes.json", "insider.json",
    "scorecard.json", "drops.json",
]


def _stamp(raw: bytes) -> datetime | None:
    """파일이 마지막으로 손질된 시각.

    generated_at('언제 계산했나')만 보면 **계산 뒤에 덧붙인 작업**을 놓친다.
    스크립트가 파일을 만든 다음 사람이나 LLM이 내용을 채워 넣는 경우가 그렇다 —
    그때 generated_at은 그대로라서 '로컬이 더 최신'을 판정하지 못하고 방금 한
    작업을 통째로 덮어쓴 적이 있다. 그래서 modified_at도 함께 본다.
    """
    try:
        doc = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    stamps = []
    for key in ("generated_at", "modified_at"):
        v = doc.get(key) if isinstance(doc, dict) else None
        try:
            if v:
                stamps.append(datetime.fromisoformat(v))
        except (ValueError, TypeError):
            continue
    return max(stamps) if stamps else None


def main():
    os.makedirs(DIST, exist_ok=True)
    print(f"{SITE} → {DIST}\n")
    pulled = skipped = missing = 0

    for name in FILES:
        path = os.path.join(DIST, name)
        try:
            req = urllib.request.Request(f"{SITE}/{name}",
                                         headers={"User-Agent": "pull-live/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            print(f"  {name:20s} 사이트에 없음 ({e.code}) — 로컬 파일을 그대로 둡니다")
            missing += 1
            continue
        except Exception as e:
            print(f"  {name:20s} 받기 실패: {e}")
            missing += 1
            continue

        live = _stamp(raw)
        local = None
        if os.path.exists(path):
            with open(path, "rb") as f:
                local = _stamp(f.read())

        # 로컬이 더 최신이면 방금 만든 결과라는 뜻 — 덮어쓰지 않는다
        if live and local and local > live:
            print(f"  {name:20s} 로컬이 더 최신 ({local:%m-%d %H:%M} > "
                  f"{live:%m-%d %H:%M} UTC) — 유지")
            skipped += 1
            continue

        with open(path, "wb") as f:
            f.write(raw)
        age = f"{live:%m-%d %H:%M} UTC" if live else "시각 없음"
        print(f"  {name:20s} 받음 {len(raw):>8,}B  ({age})")
        pulled += 1

    print(f"\n받음 {pulled} · 로컬 유지 {skipped} · 없음 {missing}")
    print("이제 로컬에서 배포해도 자동화 결과를 덮어쓰지 않습니다.")


if __name__ == "__main__":
    main()
