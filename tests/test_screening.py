"""촉매 분류와 금액 파싱.

정규식으로 '독해'를 하려다 두 번 실패한 자리다(events.py, 과거 재생). 지금 남아 있는
정규식은 **보조 판정**만 하므로, 최소한 뻔한 오탐·누락은 잠가둔다.
"""
import unittest

from src import events
import screen_news


class ParseAmountsTest(unittest.TestCase):
    def test_단위별_환산(self):
        self.assertEqual(events.parse_amounts("a $8 billion deal"), [8e9])
        self.assertEqual(events.parse_amounts("worth $200M"), [2e8])
        self.assertEqual(events.parse_amounts("US$1.5 trillion"), [1.5e12])
        self.assertEqual(events.parse_amounts("$500 thousand"), [5e5])

    def test_쉼표와_소수점(self):
        self.assertEqual(events.parse_amounts("$1,250.5 million"), [1.2505e9])

    def test_여러_금액을_모두_뽑는다(self):
        self.assertEqual(sorted(events.parse_amounts("$2B up from $500M")),
                         [5e8, 2e9])

    def test_단위_없는_숫자는_무시(self):
        """'$26'처럼 단위가 없으면 이벤트 규모로 볼 수 없다."""
        self.assertEqual(events.parse_amounts("price target to $26"), [])

    def test_금액이_없으면_빈_목록(self):
        self.assertEqual(events.parse_amounts("no money here"), [])
        self.assertEqual(events.parse_amounts(""), [])
        self.assertEqual(events.parse_amounts(None), [])


class ClassifyTest(unittest.TestCase):
    def test_유입_사건(self):
        self.assertEqual(events.classify("Generac wins supply contract")[0], "계약 수주")
        self.assertEqual(events.classify("Alcoa to acquire South32 assets")[0], "인수·합병")

    def test_유출_사건도_같은_기준으로_잡는다(self):
        """호재만 잡고 악재를 놓치면 그건 사실상 종목 추천이 된다."""
        self.assertEqual(events.classify("faces class action lawsuit")[1], "outflow")
        self.assertEqual(events.classify("issued a recall for defect")[1], "outflow")
        self.assertEqual(events.classify("hit with a $10M fine")[1], "outflow")

    def test_아마존_공급계약을_놓치지_않는다(self):
        """예전에 deal/agreement/supply가 빠져 있어 80억 달러 계약을 놓쳤다."""
        label, direction = events.classify(
            "Generac enters into $8 billion deal with Amazon to support data centers")
        self.assertIsNotNone(label)
        self.assertEqual(direction, "inflow")

    def test_투자의견_to_Buy를_인수로_오인하지_않는다(self):
        """실제로 겪은 오분류. 'to buy'만 보면 애널리스트 의견이 인수·합병이 된다."""
        self.assertEqual(
            events.classify("Jefferies Upgrades Generac to Buy, Raises Price Target to $302"),
            (None, None))
        self.assertEqual(events.classify("B of A Downgrades Alcoa to Underperform")[0], None)

    def test_자금조달을_헐겁게_잡지_않는다(self):
        """예전 패턴(rais\\w+|offering|grant)은 기사 279건 중 62건을 잡았고 대부분 오탐이었다."""
        for text in [
            "Jefferies Raises Price Target to $302",       # 목표주가 상향
            "Company raises FY guidance",                   # 가이던스 상향
            "offering customers a new plan",                # 고객에게 제공
            "granted a patent for the design",              # 특허 취득
        ]:
            self.assertNotEqual(events.classify(text)[0], "자금 조달",
                                f"오탐: {text}")

    def test_진짜_자금조달은_잡는다(self):
        for text, why in [
            ("Alcoa prices $2.6 billion of senior notes", "회사채 발행"),
            ("raises $500 million in a private placement", "유상증자"),
            ("Super Micro files prospectus for mixed shelf offering", "주식 발행 등록"),
            ("Generac awarded up to $200M grant for storage", "정부 보조금"),
            ("closed a $50 million funding round", "투자 유치"),
        ]:
            self.assertIsNotNone(events.classify(text)[0], f"놓침({why}): {text}")

    def test_진짜_인수는_여전히_잡는다(self):
        self.assertEqual(events.classify("Qualcomm to buy Arduino")[0], "인수·합병")
        self.assertEqual(events.classify("Alcoa To Acquire South32 assets")[0], "인수·합병")
        self.assertEqual(events.classify("announces merger with Rival Inc")[0], "인수·합병")

    def test_해당_없으면_None(self):
        self.assertEqual(events.classify("The weather is nice"), (None, None))


class CatalystTierTest(unittest.TestCase):
    @staticmethod
    def _news(*headlines):
        return [{"headline": h, "summary": ""} for h in headlines]

    def test_뉴스가_없으면_마이너스1(self):
        self.assertEqual(screen_news.catalyst_tier([]), (-1, None))

    def test_금액이_명시된_사건이_최상위(self):
        tier, kind = screen_news.catalyst_tier(
            self._news("Generac wins $8 billion contract with Amazon"))
        self.assertEqual(tier, 3)
        self.assertIsNotNone(kind)

    def test_실적은_2단계(self):
        tier, kind = screen_news.catalyst_tier(
            self._news("Crocs Q2 EPS beats estimate"))
        self.assertEqual(tier, 2)

    def test_애널리스트_의견은_1단계(self):
        tier, kind = screen_news.catalyst_tier(
            self._news("Jefferies upgrades Generac to Buy"))
        self.assertEqual(tier, 1)

    def test_분류_안_되는_뉴스는_0단계(self):
        tier, _ = screen_news.catalyst_tier(self._news("A quiet day for the stock"))
        self.assertEqual(tier, 0)

    def test_여러_뉴스_중_가장_강한_것을_고른다(self):
        tier, _ = screen_news.catalyst_tier(self._news(
            "Analyst raises rating",
            "Company wins $500 million order",
        ))
        self.assertEqual(tier, 3)


if __name__ == "__main__":
    unittest.main()
