"""Tests for grade, speech, and visual-QC helpers.

Pure-logic tests run instantly; the integration tests generate tiny lavfi
clips with the same ffmpeg the engine uses, so they also prove the filter
strings execute on THIS machine's build. Run:

    uv run python -m unittest discover tests -v
"""
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from reelly import config, grade, media, speech, visual_qc
from reelly.judge import check_file


def _make_clip(path, dur=3.0, size="320x240", hdr=False, dark=False):
    """Tiny test clip: testsrc2 video + 440Hz sine audio."""
    vf = "eq=brightness=-0.35" if dark else "null"
    if hdr:  # tag HDR transfer metadata the way an untonemapped iPhone file carries it
        # (output -color_trc flags don't reach the VUI on this build; setparams does)
        vf += ",setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc"
    args = [config.FFMPEG, "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={dur}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
            "-vf", vf, "-pix_fmt", "yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac"]
    subprocess.run(args + [path], check=True)
    return path


class SnapLadder(unittest.TestCase):
    """C8: silence-safety ladder + ASR drift padding in speech.snap_*."""

    def test_prefers_clean_silence_over_closer_short_one(self):
        # a 100ms silence sits closer to the cut than a 500ms one; the ladder
        # must pick the clean >=400ms silence, not the near noise
        sil = [(9.95, 10.05), (9.0, 9.5)]  # (start, end)
        out = speech.snap_start(10.1, sil, window=1.5)
        self.assertAlmostEqual(out, 9.5 - 0.10, places=3)

    def test_mid_tier_silence_is_a_fallback(self):
        sil = [(9.7, 9.95)]  # 250ms: usable when nothing clean exists
        out = speech.snap_start(10.0, sil, window=0.7)
        self.assertAlmostEqual(out, 9.95 - 0.10, places=3)

    def test_sub_150ms_never_snapped_drift_pad_applies(self):
        sil = [(9.9, 10.0)]  # 100ms of nothing: not a cut home
        out = speech.snap_start(10.0, sil, window=0.7)
        self.assertAlmostEqual(out, 10.0 - 0.08, places=3)  # padded outward

    def test_snap_end_pads_forward_without_silence(self):
        self.assertAlmostEqual(speech.snap_end(20.0, [], window=0.7),
                               20.12, places=3)

    def test_snap_start_never_negative(self):
        self.assertEqual(speech.snap_start(0.02, [], window=0.7), 0.0)


class AutoGrade(unittest.TestCase):
    """Bounded corrective grade: direction right, caps hard, no taste."""

    def _grade_for(self, stats):
        with mock.patch.object(grade, "_frame_stats", return_value=stats):
            return grade.auto_grade("x.mp4", 0, 10)

    def test_dark_flat_footage_gets_lift_within_caps(self):
        flt, _ = self._grade_for({"y_mean": 0.30, "y_range": 0.50, "sat_mean": 0.15})
        self.assertIn("gamma=1.100", flt)      # max lift, exactly at the cap
        self.assertIn("contrast=1.080", flt)   # max contrast, at the cap
        self.assertIn("saturation=1.040", flt)

    def test_balanced_footage_gets_baseline_only(self):
        flt, _ = self._grade_for({"y_mean": 0.50, "y_range": 0.72, "sat_mean": 0.25})
        self.assertIn("contrast=1.030", flt)
        self.assertNotIn("gamma", flt)

    def test_hot_punchy_footage_pulled_back_not_crushed(self):
        flt, _ = self._grade_for({"y_mean": 0.70, "y_range": 0.90, "sat_mean": 0.45})
        self.assertIn("gamma=0.970", flt)
        self.assertIn("saturation=0.960", flt)

    def test_failed_analysis_means_no_grade(self):
        with mock.patch.object(grade, "_frame_stats", return_value=None):
            self.assertEqual(grade.auto_grade("x.mp4", 0, 10), ("", None))

    def test_real_signalstats_on_dark_clip(self):
        with tempfile.TemporaryDirectory() as td:
            clip = _make_clip(os.path.join(td, "dark.mp4"), dark=True)
            flt, stats = grade.auto_grade(clip, 0, 3)
            self.assertIsNotNone(stats)
            self.assertLess(stats["y_mean"], 0.42)  # it IS dark
            self.assertIn("gamma", flt)             # and it gets lifted


class Boundaries(unittest.TestCase):
    """visual_qc._boundaries: joins on the OUTPUT timeline, speed-aware."""

    def test_plain_segments(self):
        plan = {"segments": [[5.0, 10.0], [20.0, 24.0], [30.0, 31.0]]}
        self.assertEqual(visual_qc._boundaries(plan), [5.0, 9.0])

    def test_speed_remap_compresses_the_timeline(self):
        plan = {"segments": [[0.0, 10.0, 2.0], [20.0, 21.0]]}
        self.assertEqual(visual_qc._boundaries(plan), [5.0])

    def test_single_segment_has_no_joins(self):
        self.assertEqual(visual_qc._boundaries({"segments": [[0, 9]]}), [])


class HdrHandling(unittest.TestCase):
    """P6: detection always works; the chain only runs where the build can."""

    def test_filter_capability_probe(self):
        self.assertTrue(media.has_filter("afade"))
        self.assertFalse(media.has_filter("definitely_not_a_filter"))

    def test_sdr_source_detected_and_needs_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            clip = _make_clip(os.path.join(td, "sdr.mp4"))
            self.assertFalse(media.is_hdr(clip))
            self.assertEqual(media.sdr_chain(clip), "")

    def test_hdr_source_detected(self):
        with tempfile.TemporaryDirectory() as td:
            clip = _make_clip(os.path.join(td, "hdr.mp4"), hdr=True)
            self.assertTrue(media.is_hdr(clip))
            chain = media.sdr_chain(clip)
            if media.has_filter("zscale"):
                self.assertEqual(chain, media.TONEMAP)
            else:  # lean build: no chain, and the judge gate must catch it
                self.assertEqual(chain, "")

    def test_judge_gate_fails_hdr_deliverable(self):
        with tempfile.TemporaryDirectory() as td:
            clip = _make_clip(os.path.join(td, "hdr.mp4"), hdr=True)
            results = dict((g, s) for g, s, _ in check_file(clip, expect_vertical=False)["results"])
            self.assertEqual(results["sdr_transfer"], "FAIL")

    def test_judge_gate_passes_sdr_deliverable(self):
        with tempfile.TemporaryDirectory() as td:
            clip = _make_clip(os.path.join(td, "sdr.mp4"))
            results = dict((g, s) for g, s, _ in check_file(clip, expect_vertical=False)["results"])
            self.assertEqual(results["sdr_transfer"], "PASS")


class BoundaryFades(unittest.TestCase):
    """S7: the fade-wrapped extract commands actually run on this build."""

    def test_cut_segments_renders_with_fades(self):
        from reelly.preview import _cut_segments
        with tempfile.TemporaryDirectory() as td:
            clip = _make_clip(os.path.join(td, "src.mp4"), dur=4.0)
            dst = os.path.join(td, "out.mp4")
            _cut_segments(clip, [[0.2, 1.4], [2.0, 3.1]], dst)
            self.assertTrue(os.path.exists(dst))
            self.assertAlmostEqual(media.duration(dst), 2.3, delta=0.25)

    def test_first_audio_sample_is_faded(self):
        # volumedetect over just the first 15ms should sit well below the
        # clip average if the fade-in is real
        from reelly.preview import _cut_segments
        with tempfile.TemporaryDirectory() as td:
            clip = _make_clip(os.path.join(td, "src.mp4"), dur=3.0)
            dst = os.path.join(td, "out.mp4")
            _cut_segments(clip, [[0.5, 2.5]], dst)

            def mean_vol(args):
                r = subprocess.run([config.FFMPEG, "-i", dst, *args,
                                    "-af", "volumedetect", "-f", "null", "-"],
                                   capture_output=True, text=True)
                for line in r.stderr.splitlines():
                    if "mean_volume" in line:
                        return float(line.split("mean_volume:")[1].split()[0])
                return None

            head = mean_vol(["-t", "0.015"])
            whole = mean_vol([])
            self.assertIsNotNone(head)
            self.assertLess(head, whole - 6)  # fade head is much quieter


class Composite(unittest.TestCase):
    """Visual QC composite renders standalone (no Gemini call)."""

    def test_composite_png_written(self):
        with tempfile.TemporaryDirectory() as td:
            clip = _make_clip(os.path.join(td, "src.mp4"), dur=4.0)
            words = [{"t": "hello", "s": 1.0, "e": 1.4},
                     {"t": "world", "s": 2.6, "e": 3.0}]
            out = visual_qc.composite(clip, 2.0, os.path.join(td, "j.png"),
                                      words=words, title="src.mp4")
            self.assertTrue(out and os.path.getsize(out) > 10_000)

    def test_window_too_small_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            clip = _make_clip(os.path.join(td, "src.mp4"), dur=0.5)
            self.assertIsNone(
                visual_qc.composite(clip, 0.1, os.path.join(td, "j.png")))


if __name__ == "__main__":
    unittest.main()
