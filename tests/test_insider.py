"""내부자 거래(Form 4) 파싱.

여기서 지켜야 할 것: **거래 코드를 뭉뚱그리지 않는다.** 보상으로 받은 주식(A),
세금납부용 원천징수(F), 옵션 행사(M)를 '매도'로 세면 "임원이 팔았다"는 완전히
잘못된 그림이 된다. 초보가 놀라는 경우 대부분이 F다.
"""
import unittest
from unittest import mock

from src import insider

FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Tan Lip-Bu</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector>
      <isOfficer>1</isOfficer>
      <officerTitle>Chief Executive Officer</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-08-11</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>105263</value></transactionShares>
        <transactionPricePerShare><value>95.00</value></transactionPricePerShare>
      </transactionAmounts>
      <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>205263</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-08-12</value></transactionDate>
      <transactionCoding><transactionCode>F</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>96.00</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""

INDEX_JSON = {"directory": {"item": [{"name": "form4.xml"}, {"name": "x.txt"}]}}


class ParseForm4Test(unittest.TestCase):
    def _parse(self):
        def fake_get(url, as_json=False):
            return INDEX_JSON if as_json else FORM4_XML
        with mock.patch.object(insider, "_get", side_effect=fake_get), \
             mock.patch.object(insider.time, "sleep"):
            return insider.parse_form4("0000050863", "0000050863-26-000001")

    def test_신고자와_직위를_읽는다(self):
        txs = self._parse()
        self.assertEqual(txs[0]["owner"], "Tan Lip-Bu")
        self.assertEqual(txs[0]["title"], "Chief Executive Officer")

    def test_장내매수는_금액까지_계산한다(self):
        buy = self._parse()[0]
        self.assertEqual(buy["code"], "P")
        self.assertEqual(buy["label"], "장내 매수")
        self.assertEqual(buy["shares"], 105263)
        self.assertEqual(buy["value"], round(105263 * 95.00))

    def test_세금납부용_원천징수는_매도가_아니다(self):
        tax = self._parse()[1]
        self.assertEqual(tax["code"], "F")
        self.assertEqual(tax["label"], "세금납부용 원천징수")
        self.assertNotIn(tax["code"], insider.MEANINGFUL,
                         "F를 '매도'로 세면 완전히 잘못된 그림이 된다")

    def test_본인_판단_거래만_MEANINGFUL이다(self):
        self.assertEqual(insider.MEANINGFUL, {"P", "S"})
        for code in ("A", "M", "F", "G", "C"):
            self.assertNotIn(code, insider.MEANINGFUL)

    def test_모든_코드에_한국어_이름이_있다(self):
        for code in ("P", "S", "A", "M", "F", "G", "C"):
            self.assertIn(code, insider.CODE_LABEL)
            self.assertTrue(insider.CODE_LABEL[code].strip())


if __name__ == "__main__":
    unittest.main()
