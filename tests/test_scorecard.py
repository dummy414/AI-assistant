"""성적표의 기준봉 잡기.

'선정 후 N일 수익률'은 어느 봉을 기준으로 삼느냐로 값이 통째로 달라진다.
카드는 **선정 날짜 직전 거래일 종가**를 보고 쓰였으므로 기준봉도 거기여야 한다.
하루만 밀려도 성적이 조용히 좋아지거나 나빠진다.
"""
import unittest

import pandas as pd

from src import scorecard


def bars(dates, closes):
    return pd.DataFrame({"close": closes}, index=pd.to_datetime(dates))


class ForwardReturnsTest(unittest.TestCase):
    def setUp(self):
        # 09-14(월) ~ 09-18(금)
        self.df = bars(
            ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"],
            [100.0, 110.0, 120.0, 130.0, 140.0],
        )

    def test_기준봉은_선정일_직전_거래일이다(self):
        # 09-16에 선정 → 기준은 09-15(110), +1일은 09-16(120)
        out = scorecard._forward_returns(self.df, "2026-09-16")
        self.assertAlmostEqual(out[1], 120 / 110 - 1, places=6)

    def test_5거래일_뒤가_없으면_그_항목은_빠진다(self):
        out = scorecard._forward_returns(self.df, "2026-09-16")
        self.assertIn(1, out)
        self.assertNotIn(5, out, "봉이 모자라면 없는 값을 지어내면 안 된다")

    def test_5거래일_뒤가_있으면_계산한다(self):
        df = bars([f"2026-09-{d:02d}" for d in (14, 15, 16, 17, 18, 21, 22)],
                  [100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 160.0])
        out = scorecard._forward_returns(df, "2026-09-15")
        # 기준 09-14(100), +5는 09-21(150)
        self.assertAlmostEqual(out[5], 0.5, places=6)

    def test_선정일이_첫_봉보다_이르면_측정하지_않는다(self):
        self.assertEqual(scorecard._forward_returns(self.df, "2026-09-14"), {})
        self.assertEqual(scorecard._forward_returns(self.df, "2026-01-01"), {})

    def test_거래일이_아닌_날짜로_들어와도_직전_거래일을_기준으로_잡는다(self):
        """토요일(09-19)로 기록된 건이 있어도 기준봉은 금요일(09-18)이다.
        그 뒤에 봉이 없으니 아직 측정할 수 없고, 빈 값이 나와야 한다."""
        self.assertEqual(scorecard._forward_returns(self.df, "2026-09-19"), {})

    def test_기준봉_다음_봉이_있으면_토요일_날짜여도_측정된다(self):
        df = bars([f"2026-09-{d:02d}" for d in (17, 18, 21)], [100.0, 110.0, 121.0])
        out = scorecard._forward_returns(df, "2026-09-19")
        # 기준 09-18(110), +1은 09-21(121)
        self.assertAlmostEqual(out[1], 0.1, places=6)


if __name__ == "__main__":
    unittest.main()
