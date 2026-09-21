"""history.py — 날짜 키와 중복 제거.

여기서 잡으려는 실제 사고: 금요일 데이터를 금요일 밤과 토요일 밤에 각각 기록해서
하루가 이틀로 세어졌다. 화면에 거짓 '2일째 선정'이 뜨고, 성적표 표본도 두 배가 됐다.
"""
import json
import os
import tempfile
import unittest

from src import history


class DateKeyTest(unittest.TestCase):
    def test_market_date를_우선_쓴다(self):
        """실행일이 아니라 거래일이 기준이다 — 토요일 밤에 돌아도 금요일로 기록돼야 한다."""
        self.assertEqual(
            history._date_key({"market_date": "2026-09-18",
                               "generated_at": "2026-09-19T22:16:00+00:00"}),
            "2026-09-18")

    def test_옛_한국어_날짜는_generated_at으로_물러난다(self):
        self.assertEqual(
            history._date_key({"market_date": "2026년 9월 16일",
                               "generated_at": "2026-09-17T03:27:00+00:00"}),
            "2026-09-17")

    def test_market_date가_없으면_generated_at(self):
        self.assertEqual(
            history._date_key({"generated_at": "2026-09-18T22:19:00+00:00"}),
            "2026-09-18")

    def test_둘_다_없으면_빈_문자열(self):
        self.assertEqual(history._date_key({}), "")


class BuildDedupeTest(unittest.TestCase):
    """같은 거래일을 두 번 기록하지 않는다."""

    def setUp(self):
        self._real_fetch = history._fetch_live_history

    def tearDown(self):
        history._fetch_live_history = self._real_fetch

    def _build_with(self, existing_days, today_doc):
        history._fetch_live_history = lambda: existing_days
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "data.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(today_doc, f)
            return history.build(path)

    def test_같은_거래일은_덮어쓴다(self):
        existing = [{
            "date": "2026-09-18", "market_date": "2026-09-18",
            "generated_at": "2026-09-18T22:19:00+00:00",
            "stocks": [{"symbol": "QCOM"}],
        }]
        today = {
            "market_date": "2026-09-18",              # 토요일 밤 실행 — 같은 거래일
            "generated_at": "2026-09-19T22:16:00+00:00",
            "cards": [{"symbol": "APLD"}, {"symbol": "NFLX"}],
        }
        out = self._build_with(existing, today)
        self.assertEqual([d["date"] for d in out["days"]], ["2026-09-18"],
                         "금요일이 두 번 기록되면 안 된다")
        self.assertEqual([s["symbol"] for s in out["days"][0]["stocks"]],
                         ["APLD", "NFLX"], "나중 실행분이 남아야 한다")

    def test_다른_거래일은_이어붙인다(self):
        existing = [{
            "date": "2026-09-18", "market_date": "2026-09-18",
            "generated_at": "2026-09-18T22:19:00+00:00",
            "stocks": [{"symbol": "QCOM"}],
        }]
        today = {
            "market_date": "2026-09-21",
            "generated_at": "2026-09-21T22:10:00+00:00",
            "cards": [{"symbol": "AAPL"}],
        }
        out = self._build_with(existing, today)
        self.assertEqual([d["date"] for d in out["days"]],
                         ["2026-09-18", "2026-09-21"])

    def test_예전에_실행일로_잘못_저장된_기록도_거래일로_다시_잡는다(self):
        """이미 쌓인 잘못된 기록이 새 기록과 중복되지 않아야 한다."""
        existing = [{
            "date": "2026-09-19",                     # 실행일로 잘못 저장됨
            "market_date": "2026-09-18",              # 실제 거래일은 금요일
            "generated_at": "2026-09-19T22:16:00+00:00",
            "stocks": [{"symbol": "RIOT"}],
        }]
        today = {
            "market_date": "2026-09-18",
            "generated_at": "2026-09-20T22:05:00+00:00",
            "cards": [{"symbol": "HUT"}],
        }
        out = self._build_with(existing, today)
        self.assertEqual([d["date"] for d in out["days"]], ["2026-09-18"])


class SummarizeTest(unittest.TestCase):
    @staticmethod
    def _hist(days):
        return {"days": days}

    def test_연속_등장과_첫_등장_이후_변화(self):
        h = self._hist([
            {"date": "2026-09-16", "stocks": [{"symbol": "A", "price": 100.0}]},
            {"date": "2026-09-17", "stocks": [{"symbol": "B", "price": 50.0}]},
            {"date": "2026-09-18", "stocks": [{"symbol": "A", "price": 110.0}]},
            {"date": "2026-09-21", "stocks": [{"symbol": "A", "price": 121.0}]},
        ])
        st = history.summarize(h)
        self.assertEqual(st["A"]["count"], 3)
        self.assertEqual(st["A"]["streak"], 2, "마지막 두 날 연속 등장")
        self.assertEqual(st["A"]["first_seen"], "2026-09-16")
        self.assertAlmostEqual(st["A"]["change_since_first"], 0.21, places=4)

    def test_오늘_없으면_연속은_0(self):
        h = self._hist([
            {"date": "2026-09-18", "stocks": [{"symbol": "B", "price": 10.0}]},
            {"date": "2026-09-21", "stocks": [{"symbol": "A", "price": 10.0}]},
        ])
        st = history.summarize(h)
        self.assertEqual(st["B"]["streak"], 0)
        self.assertEqual(st["A"]["streak"], 1)

    def test_가격이_없으면_변화는_None(self):
        h = self._hist([{"date": "2026-09-18", "stocks": [{"symbol": "A"}]}])
        self.assertIsNone(history.summarize(h)["A"]["change_since_first"])


if __name__ == "__main__":
    unittest.main()
