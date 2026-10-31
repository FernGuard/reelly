# Third-party notices

The MIT License applies to Reelly's own source code. Reelly vendors no
third-party source directory: dependencies install from their registries,
models download from their publishers (`reelly models fetch`), and tools you
install yourself, each under its own license and terms.

## Adapted code

- `reelly/grade.py` and `reelly/visual_qc.py` were adapted from
  [browser-use/video-use](https://github.com/browser-use/video-use)
  (`helpers/grade.py` and `helpers/timeline_view.py`), MIT License.
- The noise hashes in `reelly/effects/lib/noise.glsl`, `lib/finish.glsl`, the
  effect files and `reelly/gputext.py` follow Dave Hoskins' "Hash without Sine"
  (https://www.shadertoy.com/view/4djSRW), MIT License:

  > Copyright (c) 2014 David Hoskins.
  >
  > Permission is hereby granted, free of charge, to any person obtaining a copy
  > of this software and associated documentation files (the "Software"), to deal
  > in the Software without restriction, including without limitation the rights
  > to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
  > copies of the Software, and to permit persons to whom the Software is
  > furnished to do so, subject to the following conditions:
  >
  > The above copyright notice and this permission notice shall be included in
  > all copies or substantial portions of the Software.
  >
  > THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
  > IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
  > FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
  > AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
  > LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
  > OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
  > SOFTWARE.

## Methods

Reelly's implementations are its own; these are the ideas they rest on.

- Domain-warped fbm for the smoke field: Inigo Quilez, "Domain warping"
  (https://iquilezles.org/articles/warp/).
- Edge-aware mask refinement: K. He, J. Sun, X. Tang, "Guided Image
  Filtering", ECCV 2010 / TPAMI 2013.
- The flash check follows WCAG 2.1 Success Criterion 2.3.1 (Three Flashes or
  Below Threshold), simplified and conservative.
- The effect file header is inspired by the Interactive Shader Format (ISF,
  VIDVOX); it is not ISF-compatible.

## Models (downloaded on request, not bundled)

| Model | Used for | License |
|---|---|---|
| Depth Anything V2 Small (ONNX, `onnx-community/depth-anything-v2-small`), L. Yang et al., 2024 | `reelly parallax` | Apache-2.0. Only the Small model: the Base, Large and Giant weights are CC-BY-NC-4.0 and must not be swapped in for commercial work. |
| MediaPipe selfie segmenter | text behind the subject | Apache-2.0 |
| MediaPipe face landmarker, BlazeFace short range | face tracking for reframing | Apache-2.0 |
| pyannote speaker diarization (optional extra, gated) | who speaks when | see each model card's terms |
| Whisper via mlx-whisper (Apple Silicon) | transcription | MIT |

## Tools you install yourself

- **EA IRIS** (https://github.com/electronicarts/IRIS), BSD-3-Clause:
  photosensitivity analysis, run as an external app when installed. IRIS states
  it does not certify compliance; neither does Reelly.
- **ffmpeg / ffprobe**, LGPL or GPL depending on your build.
- **Google Chrome or Chromium**, used headless to render type and effects.
- Optional: the Codex CLI or Claude CLI (your own subscription), DaVinci
  Resolve, Tesseract, yt-dlp.

## Python dependencies

Installed from PyPI under their own licenses, including NumPy and SciPy (BSD),
Pillow (MIT-CMU), websockets (BSD-3-Clause), MediaPipe (Apache-2.0),
onnxruntime (MIT, optional `depth` extra), requests (Apache-2.0) and the
optional pyannote, parakeet-mlx and PRISM extras.

## What the MIT License does not cover

- third-party packages, executables or services;
- model weights;
- fonts, music, sound effects, footage, logos, or generated assets;
- provider APIs or their output;
- user-supplied content.

Before distributing a build or output, review the licenses and commercial-use
terms for every dependency, model, asset and provider used in that workflow.
