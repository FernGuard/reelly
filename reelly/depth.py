"""Monocular depth for stills (2.5D parallax), $0 and offline.

Depth Anything V2 SMALL only: its code and weights are Apache-2.0 (the
Base/Large/Giant weights are CC-BY-NC-4.0, not usable for marketing).
Runs through ONNX Runtime on the CPU (onnx-community/depth-anything-V2-small,
model.onnx, ~94 MB): one still takes a few seconds, no PyTorch needed.

REELLY_DEPTH_MODEL = path to model.onnx (else the usual locations).
"""
import os

CANDIDATES = (
    r"C:\Patolli\tools\models\depth-anything-v2-small\model.onnx",
    os.path.expanduser("~/.reelly/models/depth-anything-v2-small/model.onnx"),
)
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)


def find_model():
    p = os.environ.get("REELLY_DEPTH_MODEL", "").strip()
    if p and os.path.exists(p):
        return p
    return next((c for c in CANDIDATES if os.path.exists(c)), None)


def estimate(image_path, out_png, short_side=518):
    """Relative depth of a still as a greyscale PNG the size of the image
    (white = near). Returns out_png. Raises when the model is missing."""
    import numpy as np
    import onnxruntime as ort
    from PIL import Image
    model = find_model()
    if not model:
        raise FileNotFoundError("Depth Anything V2 Small (model.onnx) not installed")
    im = Image.open(image_path).convert("RGB")
    w, h = im.size
    s = short_side / min(w, h)
    W, H = max(14, round(w * s / 14) * 14), max(14, round(h * s / 14) * 14)   # multiples of 14
    x = np.asarray(im.resize((W, H), Image.BICUBIC), dtype=np.float32) / 255.0
    x = ((x - MEAN) / STD).transpose(2, 0, 1)[None].astype(np.float32)
    sess = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
    d = sess.run(None, {sess.get_inputs()[0].name: x})[0]
    d = np.squeeze(d).astype(np.float32)
    d = (d - d.min()) / max(1e-6, float(d.max() - d.min()))
    Image.fromarray((d * 255 + 0.5).astype(np.uint8), "L").resize((w, h), Image.BICUBIC).save(out_png)
    return out_png
