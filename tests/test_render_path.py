"""Render-path perf plumbing: chunked final burn, raw-cut cache, parallel
per-cut loops, and the single-gfx-encode overlay path."""
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

from reelly import config, finalize, overlays
from reelly.preview import run_parallel


def _ev(t0, t1, png="k.png", y=1430):
    return (png, y, t0, t1)


class TestBurnChunks(unittest.TestCase):
    def test_short_video_is_one_chunk(self):
        chunks = finalize._burn_chunks([_ev(0, 3), _ev(3, 8)], 45.0)
        self.assertEqual(len(chunks), 1)
        self.assertEqual((chunks[0][0], chunks[0][1]), (0.0, 45.0))
        self.assertEqual(len(chunks[0][2]), 2)

    def test_boundaries_cover_duration_exactly(self):
        chunks = finalize._burn_chunks([], 200.0, chunk_s=75.0)
        self.assertEqual(chunks[0][:2], (0.0, 75.0))
        self.assertEqual(chunks[1][:2], (75.0, 150.0))
        self.assertEqual(chunks[-1][1], 200.0)
        for (s0, e0, _), (s1, _, _) in zip(chunks, chunks[1:]):
            self.assertEqual(e0, s1)

    def test_tiny_tail_merges_into_last_chunk(self):
        # 152s: a naive split leaves a 2s sliver; it must merge instead
        chunks = finalize._burn_chunks([], 152.0, chunk_s=75.0)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[-1][:2], (75.0, 152.0))

    def test_events_land_in_their_chunk_with_local_times(self):
        chunks = finalize._burn_chunks([_ev(10, 12), _ev(80, 82)], 160.0,
                                       chunk_s=75.0)
        self.assertEqual([(p, y, t0, t1) for p, y, t0, t1 in chunks[0][2]],
                         [("k.png", 1430, 10.0, 12.0)])
        self.assertEqual([(t0, t1) for _, _, t0, t1 in chunks[1][2]],
                         [(5.0, 7.0)])

    def test_boundary_spanning_event_appears_in_both_chunks(self):
        chunks = finalize._burn_chunks([_ev(70, 80)], 160.0, chunk_s=75.0)
        self.assertEqual([(t0, t1) for _, _, t0, t1 in chunks[0][2]],
                         [(70.0, 75.0)])
        self.assertEqual([(t0, t1) for _, _, t0, t1 in chunks[1][2]],
                         [(0.0, 5.0)])


class TestBurnFinal(unittest.TestCase):
    def _run(self, n_events, dur=45.0):
        events = [_ev(i * 0.3, i * 0.3 + 0.3, f"k{i}.png") for i in range(n_events)]
        cmds = []
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(finalize, "_burn_events", return_value=events), \
             mock.patch.object(finalize.subprocess, "run",
                               side_effect=lambda a, **k: cmds.append(a)), \
             redirect_stdout(StringIO()):
            finalize._burn_final("raw.mp4", "burn.mp4", {"id": "cut_01"},
                                 [], dur, td, None, "video")
        return cmds

    def test_small_cut_stays_single_pass(self):
        cmds = self._run(10)
        self.assertEqual(len(cmds), 1)
        cmd = cmds[0]
        self.assertNotIn("-ss", cmd)           # no seek: whole file, one pass
        self.assertIn("libx264", cmd)
        self.assertEqual(cmd.count("-i") - 1, 10)  # src + one input per event

    def test_large_cut_chunks_and_concats_with_copy(self):
        cmds = self._run(120, dur=160.0)
        # chunk encodes + one concat
        self.assertGreater(len(cmds), 2)
        for cmd in cmds[:-1]:
            self.assertIn("-ss", cmd)
            self.assertIn("libx264", cmd)
        concat = cmds[-1]
        self.assertIn("concat", concat)
        self.assertIn("copy", concat)
        self.assertNotIn("libx264", concat)

    def test_threshold_is_respected(self):
        self.assertEqual(len(self._run(finalize.BURN_CHUNK_INPUTS)), 1)


class TestKaraokeDedupe(unittest.TestCase):
    def test_identical_cues_render_their_pngs_once(self):
        wlist = [{"t": "go", "s": 0.0, "e": 0.4}, {"t": "now", "s": 0.4, "e": 0.8}]
        cues = [(0.0, 0.8, wlist), (5.0, 5.8, wlist)]  # same words twice
        calls = []
        with mock.patch.object(finalize.speech, "group_cue_words",
                               return_value=cues), \
             mock.patch.object(finalize.captions, "karaoke_png",
                               side_effect=lambda w, i, p: calls.append(p) or p):
            events = finalize._burn_events(
                {"id": "cut_01", "captions": "burned"}, [], 20.0,
                "/tmp/x", None, None)
        self.assertEqual(len(calls), 2)      # one png per highlight index
        self.assertEqual(len(events), 4)     # but all four words composited


class TestRawCache(unittest.TestCase):
    def _key(self, td, segments, bump_mtime=0, detector="facemesh",
             encode=("-c:v", "libx264")):
        src = os.path.join(td, "screen.mp4")
        if not os.path.exists(src):
            open(src, "w").write("x")
        if bump_mtime:
            t = os.path.getmtime(src) + bump_mtime
            os.utime(src, (t, t))
        # detector/encoder are probed from the environment; stub them so the
        # tests stay hermetic (no mediapipe import, no env sensitivity)
        with mock.patch.object(finalize.face, "detector_kind",
                               return_value=detector), \
             mock.patch.object(finalize.config, "intermediate_encode_args",
                               return_value=list(encode)):
            return finalize._raw_cache_key(
                {"id": "cut_01", "segments": segments}, src, None, False)

    def test_same_inputs_same_key(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self._key(td, [[0, 5]]), self._key(td, [[0, 5]]))

    def test_segments_change_busts_key(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertNotEqual(self._key(td, [[0, 5]]), self._key(td, [[0, 6]]))

    def test_source_mtime_change_busts_key(self):
        with tempfile.TemporaryDirectory() as td:
            k1 = self._key(td, [[0, 5]])
            k2 = self._key(td, [[0, 5]], bump_mtime=100)
            self.assertNotEqual(k1, k2)

    def test_detector_change_busts_key(self):
        # facemesh and blaze frame the facecam crop differently: a raw cut
        # rendered under one must miss under the other
        with tempfile.TemporaryDirectory() as td:
            self.assertNotEqual(self._key(td, [[0, 5]], detector="facemesh"),
                                self._key(td, [[0, 5]], detector="blaze"))

    def test_encoder_change_busts_key(self):
        with tempfile.TemporaryDirectory() as td:
            k_sw = self._key(td, [[0, 5]], encode=("-c:v", "libx264"))
            k_hw = self._key(td, [[0, 5]], encode=("-c:v", "h264_videotoolbox"))
            self.assertNotEqual(k_sw, k_hw)

    def test_version_bump_busts_key(self):
        with tempfile.TemporaryDirectory() as td:
            k1 = self._key(td, [[0, 5]])
            with mock.patch.object(finalize, "RAW_CACHE_VERSION",
                                   finalize.RAW_CACHE_VERSION + 1):
                k2 = self._key(td, [[0, 5]])
            self.assertNotEqual(k1, k2)

    def test_cached_raw_reuses_then_rerenders(self):
        with tempfile.TemporaryDirectory() as td:
            plan = {"id": "cut_01", "segments": [[0, 5]]}
            renders = []

            def render(dst):
                renders.append(dst)
                open(dst, "w").write("video")

            with redirect_stdout(StringIO()):
                r1 = finalize._cached_raw(td, plan, "", "key1", False, render)
                r2 = finalize._cached_raw(td, plan, "", "key1", False, render)
            self.assertEqual(r1, r2)
            self.assertEqual(len(renders), 1)          # second run reused
            self.assertTrue(r1.endswith("cut_01_raw.mp4"))
            meta = json.load(open(r1 + ".json"))
            self.assertEqual(meta["key"], "key1")
            # a changed key re-renders; force re-renders even on a match
            with redirect_stdout(StringIO()):
                finalize._cached_raw(td, plan, "", "key2", False, render)
            self.assertEqual(len(renders), 2)
            with redirect_stdout(StringIO()):
                finalize._cached_raw(td, plan, "", "key2", True, render)
            self.assertEqual(len(renders), 3)
            # one raw per cut id: overwritten, not accumulated
            cdir = os.path.join(td, "deliverables", ".cache")
            self.assertEqual(sorted(os.listdir(cdir)),
                             ["cut_01_raw.mp4", "cut_01_raw.mp4.json"])


class TestIntermediateEncodeArgs(unittest.TestCase):
    """libx264 veryfast is the intermediate default (benchmarked faster than
    videotoolbox); hardware is opt-in via REELLY_HW_ENCODE=1."""

    def _args(self, env, probe_ok=True):
        probes = []

        def probe():
            probes.append(1)
            return probe_ok

        clean = {k: v for k, v in os.environ.items()
                 if k not in ("REELLY_HW_ENCODE", "REELLY_SW_ENCODE")}
        with mock.patch.dict(os.environ, {**clean, **env}, clear=True), \
             mock.patch.object(config, "_HW_ENCODE", None), \
             mock.patch.object(config, "_videotoolbox_works", probe), \
             redirect_stdout(StringIO()):
            return config.intermediate_encode_args(), len(probes)

    def test_default_is_software_and_never_probes(self):
        args, probes = self._args({})
        self.assertIn("libx264", args)
        self.assertIn("veryfast", args)
        self.assertNotIn("h264_videotoolbox", args)
        self.assertEqual(probes, 0)     # no videotoolbox probe on the default path

    def test_hw_opt_in_probes_and_uses_videotoolbox(self):
        args, probes = self._args({"REELLY_HW_ENCODE": "1"})
        self.assertIn("h264_videotoolbox", args)
        self.assertEqual(probes, 1)

    def test_hw_opt_in_falls_back_when_probe_fails(self):
        args, _ = self._args({"REELLY_HW_ENCODE": "1"}, probe_ok=False)
        self.assertIn("libx264", args)

    def test_sw_override_beats_hw_opt_in(self):
        args, probes = self._args({"REELLY_HW_ENCODE": "1",
                                   "REELLY_SW_ENCODE": "1"})
        self.assertIn("libx264", args)
        self.assertEqual(probes, 0)


class TestRunParallel(unittest.TestCase):
    def test_results_keep_input_order(self):
        def fn(x):
            time.sleep(0.05 if x == "a" else 0.0)  # first submitted ends last
            return x.upper()

        res = run_parallel(["a", "b", "c"], fn, max_workers=3)
        self.assertEqual([(i, r, e) for i, r, e in res],
                         [("a", "A", None), ("b", "B", None), ("c", "C", None)])

    def test_one_failure_does_not_kill_siblings(self):
        def fn(x):
            if x == "b":
                raise ValueError("boom")
            return x

        res = run_parallel(["a", "b", "c"], fn, max_workers=3)
        self.assertEqual(res[0], ("a", "a", None))
        self.assertIsInstance(res[1][2], ValueError)
        self.assertEqual(res[2], ("c", "c", None))

    def test_actually_runs_concurrently(self):
        seen = set()
        gate = threading.Barrier(2, timeout=5)

        def fn(x):
            seen.add(threading.current_thread().name)
            gate.wait()   # deadlocks unless two items overlap in time
            return x

        run_parallel([1, 2], fn, max_workers=2)
        self.assertGreaterEqual(len(seen), 2)

    def test_single_item_runs_inline(self):
        res = run_parallel(["only"], lambda x: x, max_workers=3)
        self.assertEqual(res, [("only", "only", None)])


class TestOverlayApplySingleEncode(unittest.TestCase):
    def _project(self, td):
        os.makedirs(os.path.join(td, "edl"))
        final = os.path.join(td, "deliverables", "final")
        os.makedirs(final)
        json.dump({"cut_01": [{"template": "chip", "args": ["hi"],
                               "t": [1.0, 3.0], "sfx": ["pop.mp3", -14]}]},
                  open(os.path.join(td, "edl", "overlay_specs.json"), "w"))
        for fn in ("cut_01.mp4", "cut_01_trending.mp4"):
            open(os.path.join(final, fn), "w").write("v")
        return final

    def test_second_variant_reuses_the_composited_video(self):
        with tempfile.TemporaryDirectory() as td:
            final = self._project(td)
            full, variant = [], []
            with mock.patch.object(overlays, "_composite",
                                   side_effect=lambda s, o, e, w: full.append((s, o))), \
                 mock.patch.object(overlays, "_composite_variant",
                                   side_effect=lambda g, s, o, e, w: variant.append((g, s, o))), \
                 redirect_stdout(StringIO()):
                overlays.apply(td)
            # exactly ONE video encode (the first variant); the sibling copies it
            self.assertEqual(full, [(os.path.join(final, "cut_01.mp4"),
                                     os.path.join(final, "cut_01_gfx.mp4"))])
            self.assertEqual(variant, [(os.path.join(final, "cut_01_gfx.mp4"),
                                        os.path.join(final, "cut_01_trending.mp4"),
                                        os.path.join(final, "cut_01_trending_gfx.mp4"))])

    def test_variant_pass_copies_video_and_encodes_audio_only(self):
        events = [{"template": "chip", "args": ["hi"], "t": [1.0, 3.0],
                   "sfx": ["pop.mp3", -14]}]
        cmds = []
        with mock.patch.object(overlays.subprocess, "run",
                               side_effect=lambda a, **k: cmds.append(a)), \
             mock.patch("reelly.audio_post.enforce_true_peak"), \
             mock.patch("reelly.audio_post.enforce_loudness"):
            overlays._composite_variant("gfx.mp4", "trend.mp4",
                                        "trend_gfx.mp4", events, "/tmp/w")
        # the duration probe (ffprobe, for the never-stretch-the-render clamp)
        # rides the same mocked subprocess.run; only ffmpeg encodes count
        ffm = [c for c in cmds if c and c[0] == overlays.config.FFMPEG]
        self.assertEqual(len(ffm), 1)
        cmd = ffm[0]
        i = cmd.index("-c:v")
        self.assertEqual(cmd[i + 1], "copy")           # video stream copied
        self.assertNotIn("libx264", cmd)               # no second video encode
        self.assertIn("gfx.mp4", cmd)                  # video from the gfx master
        fc = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("[1:a]", fc)                     # audio from THIS variant
        self.assertIn("amix=inputs=2", fc)
        self.assertIn("alimiter", fc)

    def test_one_cut_failing_reports_and_raises_after_the_rest(self):
        with tempfile.TemporaryDirectory() as td:
            final = self._project(td)
            spec = os.path.join(td, "edl", "overlay_specs.json")
            specs = json.load(open(spec))
            specs["cut_02"] = specs["cut_01"]
            json.dump(specs, open(spec, "w"))
            open(os.path.join(final, "cut_02.mp4"), "w").write("v")
            done = []

            def comp(src, out, events, wd):
                if "cut_02" in src:
                    raise RuntimeError("boom")
                done.append(out)

            with mock.patch.object(overlays, "_composite", side_effect=comp), \
                 mock.patch.object(overlays, "_composite_variant",
                                   side_effect=lambda g, s, o, e, w: done.append(o)), \
                 redirect_stdout(StringIO()):
                with self.assertRaisesRegex(RuntimeError, "cut_02"):
                    overlays.apply(td)
            self.assertEqual(len(done), 2)  # cut_01 still fully processed


if __name__ == "__main__":
    unittest.main()
