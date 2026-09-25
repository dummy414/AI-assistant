# -*- coding: utf-8 -*-
"""지난 배포본에서 데이터 파일을 되살린다.

왜 필요한가
--------------------------------------------------------------------------
이 사이트의 데이터는 깃에 없다. **배포된 사이트 자체가 저장소**다.
자동화는 커밋하지 않고, 어제 기록이 필요하면 배포본에서 HTTP로 받아온다.

그래서 로컬 dist를 그대로 배포하면 그날 자동화가 만든 것을 덮어쓴다.
pull_live.py가 그걸 막으려고 있는데, 2026-09-23 긴급 배포 때 그걸 건너뛰고
netlify 명령을 직접 쳤다가 하루치(09-22 종목 16개 + history 4일째)를 날렸다.

Netlify는 배포마다 고유 주소를 남긴다. 덮어쓰기 전 배포를 찾아 거기서
파일을 그대로 받아오면 복구된다. 이 스크립트가 그걸 한다.

    # 어떤 배포가 있는지 보기
    npx netlify-cli api listSiteDeploys --data '{"site_id":"...","per_page":12}'

    python tools/restore_from_deploy.py <deploy_id>
    python tools/restore_from_deploy.py <deploy_id> --apply

--apply 없이 돌리면 무엇이 달라지는지만 보여주고 파일은 건드리지 않는다.
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "card_news", "dist")
SITE = "today-watchlist-kr.netlify.app"

FILES = ["data.json", "quality.json", "radar.json", "history.json",
         "company_notes.json", "insider.json", "scorecard.json", "drops.json"]


def fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "restore-from-deploy"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def describe(name: str, text: str | None) -> str:
    """파일이 '언제 것'인지 한 줄로. 날짜가 곧 덮어쓸지 말지의 판단 근거다."""
    if text is None:
        return "없음"
    try:
        d = json.loads(text)
    except Exception:
        return f"{len(text):,}자 (JSON 아님)"
    bits = []
    if isinstance(d, dict):
        if d.get("market_date"):
            bits.append("거래일 " + str(d["market_date"]))
        if d.get("generated_at"):
            bits.append("작성 " + str(d["generated_at"])[:16])
        if isinstance(d.get("days"), list):
            bits.append(f"{len(d['days'])}일치")
        if isinstance(d.get("cards"), list):
            bits.append(f"{len(d['cards'])}종목")
        if isinstance(d.get("notes"), dict):
            bits.append(f"설명 {len(d['notes'])}개")
    return " · ".join(bits) or f"{len(text):,}자"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    deploy_id = sys.argv[1].strip()
    apply = "--apply" in sys.argv
    base = f"https://{deploy_id}--{SITE}"

    print(f"되살릴 배포 : {base}")
    print(f"지금 라이브 : https://{SITE}")
    print(f"모드        : {'실제로 덮어씁니다' if apply else '미리보기 (파일 안 건드림)'}\n")

    changed = 0
    for name in FILES:
        try:
            old = fetch(f"{base}/{name}")
        except Exception as e:
            print(f"  건너뜀  {name:22} 그 배포에 없습니다 ({e})")
            continue
        try:
            live = fetch(f"https://{SITE}/{name}")
        except Exception:
            live = None

        same = (live is not None and json.loads(live) == json.loads(old))
        mark = "그대로" if same else "다름  "
        print(f"  {mark}  {name:22}")
        print(f"            되살릴 것 : {describe(name, old)}")
        print(f"            지금      : {describe(name, live)}")
        if same:
            continue
        changed += 1
        if apply:
            io.open(os.path.join(DIST, name), "w", encoding="utf-8", newline="").write(old)

    print()
    if not changed:
        print("달라진 파일이 없습니다. 되살릴 것이 없습니다.")
    elif apply:
        print(f"{changed}개 파일을 dist에 되살렸습니다.")
        print("이제 배포하세요:  python tools/deploy.py")
        print()
        print("deploy.py가 먼저 돌리는 pull_live.py는 '로컬이 더 최신이면 유지'라서,")
        print("되살린 것이 라이브보다 새 것이면(복구는 보통 그렇다) 그대로 남습니다.")
        print("반대로 라이브가 더 새 것이면 pull_live가 되살린 것을 다시 덮습니다 —")
        print("그건 되살릴 이유가 없었다는 뜻이므로 정상 동작입니다.")
    else:
        print(f"{changed}개 파일이 다릅니다. 실제로 되살리려면 뒤에 --apply 를 붙이세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
