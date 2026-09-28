import json
import unittest

from utils.llm_json import LIST_WRAPPER_KEY, parse_chat_json


class TestParseChatJson(unittest.TestCase):
    def test_plain_object(self):
        self.assertEqual(parse_chat_json('{"ok": true}'), {"ok": True})

    def test_markdown_fence(self):
        raw = 'Here is JSON:\n```json\n{"a": 1}\n```'
        self.assertEqual(parse_chat_json(raw), {"a": 1})

    def test_truncated_unterminated_string(self):
        broken = (
            '{\n  "documentType": "Excel",\n'
            '  "trade": "Shades",\n'
            '  "projectName": "Test",\n'
            '  "windowScheduleLocation": "MATRIX'
        )
        parsed = parse_chat_json(broken)
        self.assertEqual(parsed["documentType"], "Excel")
        self.assertIn("windowScheduleLocation", parsed)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            parse_chat_json("   ")

    def test_none_raises(self):
        with self.assertRaises(ValueError):
            parse_chat_json(None)

    def test_trailing_comma(self):
        self.assertEqual(parse_chat_json('{"a": 1, "b": [2, 3,],}'), {"a": 1, "b": [2, 3]})

    def test_prose_on_both_sides_of_fence(self):
        raw = 'Sure! Here you go:\n```json\n{"totalShadeCount": 47}\n```\nLet me know if you need more.'
        self.assertEqual(parse_chat_json(raw), {"totalShadeCount": 47})

    def test_bare_array_is_wrapped(self):
        self.assertEqual(parse_chat_json('[{"tag": "A-101"}]'), {LIST_WRAPPER_KEY: [{"tag": "A-101"}]})

    def test_scalar_response_raises(self):
        with self.assertRaises(ValueError):
            parse_chat_json("42")

    def test_second_fence_used_when_first_is_not_json(self):
        raw = "```\nnot json at all\n```\n```json\n{\"ok\": 1}\n```"
        self.assertEqual(parse_chat_json(raw), {"ok": 1})


if __name__ == "__main__":
    unittest.main()
