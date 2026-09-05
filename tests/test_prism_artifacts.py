"""Tests for reelly.prism.artifacts against a synthetic ffmpeg testsrc clip
(no network, no real footage needed)."""
import os
import subprocess
import tempfile
import unittest

from reelly import config
from reelly.prism import artifacts


def _make_clip(path, dur=6.0, size="640x360"):
    args = [config.FFMPEG, "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={dur}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
            path]
    subprocess.run(args, check=True)
    return path


class ArtifactsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.clip = _make_clip(os.path.join(self.tmp, "clip.mp4"))
        self.work_dir = os.path.join(self.tmp, "work")

    def test_build_produces_frames_cuts_and_wav(self):
        art = artifacts.build(self.clip, self.work_dir)

        self.assertAlmostEqual(art["duration"], 6.0, delta=0.2)

        # hook-dense offsets (0, 0.5, 1, 2, 4) + midpoint (3.0) + final second (5.0):
        # 3.0 isn't in the hook offsets so all 7 are distinct and present.
        times = [f["t"] for f in art["frames"]]
        self.assertEqual(len(art["frames"]), 7)
        self.assertEqual(times, sorted(times))
        for expected in (0, 0.5, 1, 2, 3.0, 4, 5.0):
            self.assertIn(expected, times)

        for f in art["frames"]:
            self.assertTrue(os.path.exists(f["path"]))
            self.assertGreater(os.path.getsize(f["path"]), 0)

        self.assertTrue(os.path.exists(art["audio_wav"]))
        probe = subprocess.run(
            [config.FFPROBE, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=sample_rate,channels",
             "-of", "default=noprint_wrappers=1", art["audio_wav"]],
            capture_output=True, text=True).stdout
        self.assertIn("sample_rate=16000", probe)
        self.assertIn("channels=1", probe)

        self.assertIsInstance(art["scene_cuts"], list)

    def test_build_is_cached_on_rerun(self):
        artifacts.build(self.clip, self.work_dir)
        frame_path = os.path.join(self.work_dir, "frames", "frame_00.00.jpg")
        mtime = os.path.getmtime(frame_path)

        artifacts.build(self.clip, self.work_dir)
        self.assertEqual(os.path.getmtime(frame_path), mtime)


if __name__ == "__main__":
    unittest.main()
