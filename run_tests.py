"""
run_tests.py — 파이썬·자바스크립트 테스트를 한 번에 돌린다
=====================================================================
이 프로젝트에서 나온 버그는 거의 다 **조용히 틀리는** 종류였다. 화면에 나오기
전까지 아무도 모른다:

  - 실행일을 거래일로 착각해서 토요일 날짜로 "9월 19일 미국 증시는…"이라고 씀
  - 같은 금요일을 이틀로 기록해서 '2일째 선정' 배지가 거짓으로 붙음
  - 시간외 체결을 전일 종가와 비교해서 'SPY 대비'가 등락률과 똑같아짐
  - 정규식이 "Upgrades to Buy"를 인수·합병으로, "Raises Price Target"을 자금 조달로 분류
  - pull_live가 방금 채운 번역을 덮어씀

전부 배포 후에야 발견했다. 여기 있는 테스트는 그것들을 다시 못 일어나게 잠근다.

**네트워크를 타지 않는다.** 그래야 빠르고, 시장이 열렸든 닫혔든 같은 결과가 나온다.

실행:  python run_tests.py
"""

from __future__ import annotations
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def run(label: str, cmd: list[str]) -> bool:
    print(f"\n{'=' * 64}\n{label}\n{'=' * 64}")
    try:
        r = subprocess.run(cmd, cwd=ROOT)
    except FileNotFoundError:
        print(f"  건너뜀 — {cmd[0]}를 찾을 수 없습니다")
        return True          # 없는 런타임 때문에 전체를 실패로 만들지는 않는다
    return r.returncode == 0


def main():
    ok = True
    ok &= run("파이썬 (스크리닝·기록·성적표·공시)",
              [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."])

    node = shutil.which("node")
    if node:
        ok &= run("자바스크립트 (화면 날짜·서버리스 함수)",
                  [node, "--test", "tests/js/*.test.mjs"])
    else:
        print("\nnode를 찾을 수 없어 자바스크립트 테스트를 건너뜁니다.")

    print(f"\n{'=' * 64}")
    print("전부 통과" if ok else "실패한 테스트가 있습니다")
    print("=" * 64)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
