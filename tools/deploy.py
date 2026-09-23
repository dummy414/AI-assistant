# -*- coding: utf-8 -*-
"""사이트를 배포하고, 잠금이 실제로 걸렸는지 확인한다.

왜 이 스크립트가 있는가
--------------------------------------------------------------------------
2026-09-23 새벽, **잠금 없는 옛날 ask 함수가 8시간 넘게 배포돼 있었다.**
코드에는 잠금이 멀쩡히 있었고 로컬 테스트도 전부 통과했는데, 배포된 건
그 코드가 아니었다. 원인은 두 가지가 겹친 것이다:

  1) 배포 하나가 타임아웃으로 끊겨 **함수 0개**로 올라갔다
  2) 다음 배포에서 Netlify가 "Deploying functions from cache"라며
     **캐시에 있던 예전 번들**을 도로 올렸다 — 내 새 코드는 업로드조차 안 됐다

배포 로그의 `CDN requesting ... 0 functions`가 그 신호였는데, 성공 메시지만
보고 넘어갔다. 그래서 이 스크립트는 두 가지를 강제한다:
  - 함수 캐시를 항상 무시한다 (--skip-functions-cache)
  - 배포가 끝나면 **바깥에서 실제로 찔러보고** 잠겨 있지 않으면 실패로 끝낸다

확인은 돈이 들지 않는다. 잘못된 티커를 보내서 상태코드만 본다:
    키 없음   + 잘못된 티커 -> 401  (키를 먼저 보니까 잠금이 살아 있다)
    올바른 키 + 잘못된 티커 -> 400  (키는 통과했고 티커에서 걸렸다)
둘 중 하나라도 어긋나면 배포가 잘못된 것이다. AI 호출은 일어나지 않는다.

    python tools/deploy.py                (배포 + 확인)
    python tools/deploy.py --check-only   (확인만)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARD_NEWS = os.path.join(ROOT, "card_news")
SITE = "https://today-watchlist-kr.netlify.app"
ASK = SITE + "/.netlify/functions/ask"


def owner_key() -> str | None:
    """.env에서 읽는다. 여기 적어두지 않는다 — .env는 깃에 올라가지 않는다."""
    if os.environ.get("OWNER_KEY"):
        return os.environ["OWNER_KEY"]
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip().startswith("OWNER_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return None


def post_ask(key: str | None) -> int:
    """일부러 잘못된 티커를 보낸다 — 어느 쪽으로 막히는지만 보면 되고, 돈은 안 든다."""
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["X-Owner-Key"] = key
    req = urllib.request.Request(
        ASK,
        data=json.dumps({"symbol": "!!!", "question": "확인"}).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def check() -> bool:
    key = owner_key()
    if not key:
        print("  .env에서 OWNER_KEY를 찾지 못했습니다 — 주인 통과 여부는 건너뜁니다.")

    print("\n배포된 사이트를 바깥에서 찔러봅니다 (AI 호출 없음, 비용 0원)")
    ok = True

    got = post_ask(None)
    good = got == 401
    ok &= good
    print(f"  {'OK ' if good else '실패'}  키 없이 부르기        -> {got}  (401이어야 함)")

    got = post_ask("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    good = got == 401
    ok &= good
    print(f"  {'OK ' if good else '실패'}  틀린 키로 부르기      -> {got}  (401이어야 함)")

    if key:
        got = post_ask(key)
        good = got == 400          # 키는 통과, 티커에서 걸림 = 잠금이 정상 동작
        ok &= good
        print(f"  {'OK ' if good else '실패'}  올바른 키로 부르기    -> {got}  (400이어야 함)")

    if not ok:
        print("\n" + "!" * 64)
        print("잠금이 제대로 걸리지 않았습니다. 이 상태로 두면 남이 쓴 비용이")
        print("사이트 주인에게 청구됩니다. 배포 로그에 'functions from cache'나")
        print("'0 functions'가 찍혔는지 확인하세요.")
        print("!" * 64)
    else:
        print("\n잠금 정상 — 키 없이는 못 들어갑니다.")
    return ok


def deploy() -> None:
    # flush: 아래 subprocess가 화면에 바로 쓰므로, 이 줄이 뒤로 밀리지 않게 한다
    print("1) 배포본의 최신 시세를 로컬로 가져옵니다 (자동화 결과를 덮어쓰지 않도록)\n",
          flush=True)
    subprocess.run([sys.executable, "pull_live.py"], cwd=ROOT, check=True)

    print("\n2) 배포합니다 (함수 캐시는 항상 무시합니다)", flush=True)
    cmd = [
        "npx", "--yes", "netlify-cli@latest", "deploy", "--prod",
        "--dir", "dist",
        "--functions", "netlify/functions",
        "--skip-functions-cache",       # 이게 빠지면 예전 함수가 되살아난다
    ]
    proc = subprocess.run(cmd, cwd=CARD_NEWS, shell=(os.name == "nt"),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.splitlines():
        if any(w in line for w in ("Deploy is live", "CDN requesting", "functions cache",
                                   "Error", "error:", "Website URL", "Production URL")):
            print("   " + line.strip())

    if proc.returncode != 0 or "Deploy is live" not in out:
        raise SystemExit("\n배포가 끝나지 않았습니다. 위 로그를 확인하세요.")

    # 함수가 0개로 올라가면 /ask가 사라지고, 그다음 배포가 캐시에서 옛 걸 되살린다
    if "0 functions" in out and "edge functions" in out:
        print("\n   참고: 이번 업로드에서 새로 올린 함수가 0개입니다.")
        print("   내용이 정말 안 바뀌었으면 정상이지만, 아래 확인이 진짜 판정입니다.")


def main() -> int:
    if "--check-only" not in sys.argv:
        deploy()
    return 0 if check() else 1


if __name__ == "__main__":
    raise SystemExit(main())
