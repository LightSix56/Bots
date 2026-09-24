"""Тесты настройки UTF-8 вывода."""

import io
import unittest

import console


class FakeStream:
    def __init__(self, supports_reconfigure=True, raises=None):
        self.supports_reconfigure = supports_reconfigure
        self.raises = raises
        self.calls = []

    def reconfigure(self, **kwargs):
        if self.raises:
            raise self.raises
        self.calls.append(kwargs)


class EnableUtf8Tests(unittest.TestCase):
    def test_reconfigures_given_stream(self):
        stream = FakeStream()
        console.enable_utf8(stream)
        self.assertEqual(stream.calls, [{"encoding": "utf-8", "errors": "replace"}])

    def test_stream_without_reconfigure_is_ignored(self):
        class Plain:
            pass

        console.enable_utf8(Plain())  # не должно бросить исключение

    def test_closed_stream_does_not_crash(self):
        console.enable_utf8(FakeStream(raises=ValueError("I/O operation on closed file")))

    def test_none_stream_is_ignored(self):
        console.enable_utf8(None)

    def test_safe_print_survives_unencodable_output(self):
        stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1251", errors="strict")
        console.safe_print("✅ готово", stream)
        stream.flush()


if __name__ == "__main__":
    unittest.main()
