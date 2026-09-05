"""timing.stage (print + json append) and media.probe memoization."""
import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from reelly import media, timing


class TestStage(unittest.TestCase):
    def test_prints_time_line(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with timing.stage("caption burn"):
                pass
        out = buf.getvalue()
        self.assertRegex(out, r"^\[time\] caption burn \d+(\.\d+)?s\n$")

    def test_print_only_when_no_path(self):
        # no path: nothing written anywhere, no exception
        with redirect_stdout(io.StringIO()):
            with timing.stage("mix"):
                pass

    def test_appends_to_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "timings.json")
            with redirect_stdout(io.StringIO()):
                with timing.stage("segments+concat", path):
                    pass
                with timing.stage("mux/peak", path):
                    pass
            data = json.load(open(path))
            self.assertEqual([e["label"] for e in data],
                             ["segments+concat", "mux/peak"])
            for e in data:
                self.assertIsInstance(e["seconds"], float)
                self.assertGreaterEqual(e["seconds"], 0.0)
                self.assertRegex(e["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def test_appends_even_on_exception(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "timings.json")
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError):
                    with timing.stage("boom", path):
                        raise ValueError("stage body failed")
            data = json.load(open(path))
            self.assertEqual(data[0]["label"], "boom")

    def test_recovers_from_corrupt_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "timings.json")
            open(path, "w").write("not json{")
            with redirect_stdout(io.StringIO()):
                with timing.stage("after corruption", path):
                    pass
            data = json.load(open(path))
            self.assertEqual([e["label"] for e in data], ["after corruption"])


class TestProbeMemo(unittest.TestCase):
    def _fake_sh(self):
        r = mock.Mock()
        r.stdout = '{"format": {"duration": "12.5"}, "streams": []}'
        return mock.Mock(return_value=r)

    def test_same_file_probed_once(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
            f.write(b"stub video bytes")
            f.flush()
            fake = self._fake_sh()
            with mock.patch.object(media, "sh", fake):
                a = media.probe(f.name)
                b = media.probe(f.name)
            self.assertEqual(fake.call_count, 1)
            self.assertEqual(a, b)
            self.assertEqual(a["format"]["duration"], "12.5")

    def test_cached_result_not_shared_mutable(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
            f.write(b"stub video bytes 2")
            f.flush()
            with mock.patch.object(media, "sh", self._fake_sh()):
                a = media.probe(f.name)
                a["format"]["duration"] = "tampered"
                b = media.probe(f.name)
            self.assertEqual(b["format"]["duration"], "12.5")

    def test_changed_file_reprobed(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
            f.write(b"v1")
            f.flush()
            fake = self._fake_sh()
            with mock.patch.object(media, "sh", fake):
                media.probe(f.name)
                f.write(b" now longer")  # size (and mtime) change -> new key
                f.flush()
                media.probe(f.name)
            self.assertEqual(fake.call_count, 2)

    def test_missing_file_not_cached(self):
        fake = self._fake_sh()
        fake.return_value.stdout = "{}"
        missing = os.path.join(tempfile.gettempdir(), "reelly-does-not-exist.mp4")
        with mock.patch.object(media, "sh", fake):
            self.assertEqual(media.probe(missing), {})
            self.assertEqual(media.probe(missing), {})
        self.assertEqual(fake.call_count, 2)


if __name__ == "__main__":
    unittest.main()
