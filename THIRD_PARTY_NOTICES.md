# Third-party notices

The Reelly source tree does not contain a vendored third-party source directory. Dependencies are installed from their package registries and remain governed by their own licenses and terms.

Major runtime integrations include ffmpeg, Google Gemini, OpenAI, FAL, Anthropic, Hugging Face, MediaPipe, DaVinci Resolve, Chrome/Chromium, and optional pyannote, yt-dlp, Tesseract, and platform-specific OCR libraries.

`reelly/grade.py` and `reelly/visual_qc.py` were adapted from
[browser-use/video-use](https://github.com/browser-use/video-use)
(`helpers/grade.py` and `helpers/timeline_view.py`), MIT License.

The MIT License applies to Reelly's own source code. It does not relicense:

- third-party packages or executables;
- model weights;
- fonts, music, sound effects, footage, logos, or generated assets;
- provider APIs or their output;
- user-supplied content.

Before distributing a build or output, review the licenses and commercial-use terms for every dependency, model, asset, and provider used in that workflow.
