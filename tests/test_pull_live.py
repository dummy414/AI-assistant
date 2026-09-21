"""pull_live.py — '로컬이 더 최신인가' 판단.

여기가 틀려서 실제로 사고가 났다. 번역을 다 채워 넣은 파일을 pull_live가 덮어썼는데,
generated_at('언제 계산했나')만 비교했기 때문이다. 계산 뒤에 덧붙인 작업은
modified_at으로 표시되므로 둘 중 나중 것을 봐야 한다.
"""
import json
import unittest

import pull_live


def raw(**kw):
    return json.dumps(kw).encode("utf-8")


class StampTest(unittest.TestCase):
    def test_generated_at만_있으면_그것을_쓴다(self):
        s = pull_live._stamp(raw(generated_at="2026-09-19T11:35:00+00:00"))
        self.assertEqual(s.hour, 11)
        self.assertEqual(s.minute, 35)

    def test_modified_at이_더_나중이면_그것을_쓴다(self):
        s = pull_live._stamp(raw(generated_at="2026-09-19T11:35:00+00:00",
                                 modified_at="2026-09-19T11:53:00+00:00"))
        self.assertEqual(s.minute, 53, "계산 뒤 덧붙인 작업을 놓치면 덮어쓰기 사고가 난다")

    def test_generated_at이_더_나중일_수도_있다(self):
        s = pull_live._stamp(raw(generated_at="2026-09-20T01:00:00+00:00",
                                 modified_at="2026-09-19T11:53:00+00:00"))
        self.assertEqual(s.day, 20)

    def test_시각이_하나도_없으면_None(self):
        self.assertIsNone(pull_live._stamp(raw(foo="bar")))

    def test_깨진_시각은_무시하고_남은_것을_쓴다(self):
        s = pull_live._stamp(raw(generated_at="어제", modified_at="2026-09-19T11:53:00+00:00"))
        self.assertEqual(s.minute, 53)

    def test_JSON이_아니면_None(self):
        self.assertIsNone(pull_live._stamp(b"<html>not json</html>"))

    def test_JSON이지만_객체가_아니면_None(self):
        self.assertIsNone(pull_live._stamp(b"[1, 2, 3]"))


class GuardTest(unittest.TestCase):
    """실제 판정 규칙: 로컬이 더 최신이면 받지 않는다."""

    def test_로컬이_더_최신이면_유지해야_한다(self):
        live = pull_live._stamp(raw(generated_at="2026-09-19T11:35:00+00:00"))
        local = pull_live._stamp(raw(generated_at="2026-09-19T11:35:00+00:00",
                                     modified_at="2026-09-19T11:53:00+00:00"))
        self.assertTrue(local > live)

    def test_같은_시각이면_받아온다(self):
        live = pull_live._stamp(raw(generated_at="2026-09-19T11:35:00+00:00"))
        local = pull_live._stamp(raw(generated_at="2026-09-19T11:35:00+00:00"))
        self.assertFalse(local > live)


if __name__ == "__main__":
    unittest.main()
