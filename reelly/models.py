"""Optional local models: where they live, and `reelly models fetch`.

Each model is downloaded once from its publisher, pinned to a version and
checked against a SHA-256 before it is used, into <REELLY_HOME>/models/.
An environment variable can point at a copy elsewhere. Nothing here is
bundled with Reelly; each model keeps its own licence (THIRD_PARTY_NOTICES).
"""
import hashlib
import os
import urllib.request

from . import config

MODELS = {
    "depth": {
        "what": "Depth Anything V2 Small (ONNX): depth for `reelly parallax`",
        "file": "depth-anything-v2-small/model.onnx",
        "env": "REELLY_DEPTH_MODEL",
        "url": ("https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/"
                "4472b7362082ad9968fee890ca0f1e5aca36b93d/onnx/model.onnx"),
        "sha256": "afb6a5c28f3b6bf1618c6e43f02073ef9dfdc70e937502d51603e57b0a1df10c",
        "license": "Apache-2.0 (the Small model only; Base/Large/Giant are CC-BY-NC-4.0)",
        "needs": "onnxruntime (uv sync --extra depth)",
    },
    "segmenter": {
        "what": "MediaPipe selfie segmenter: the person mask for text behind the subject",
        "file": "selfie-segmenter/selfie_segmenter.tflite",
        "env": "REELLY_SEGMENTER",
        "url": ("https://storage.googleapis.com/mediapipe-models/image_segmenter/"
                "selfie_segmenter/float16/1/selfie_segmenter.tflite"),
        "sha256": "191ac9529ae506ee0beefa6b2c945a172dab9d07d1e802a290a4e4038226658b",
        "license": "Apache-2.0",
        "needs": "mediapipe",
    },
}


def path(key):
    """The model's file on disk (its env var first, then REELLY_HOME/models),
    or None when it is not installed."""
    m = MODELS[key]
    p = os.environ.get(m["env"], "").strip()
    if p and os.path.exists(p):
        return p
    p = os.path.join(config.HOME, "models", m["file"])
    return p if os.path.exists(p) else None


def missing_message(key):
    m = MODELS[key]
    return (f"{m['what']} is not installed. Run `reelly models fetch {key}` "
            f"(or set {m['env']} to its file). Needs {m['needs']}.")


def _sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(key):
    """Download `key` into REELLY_HOME/models (verified). Returns its path."""
    if config.local_only():
        raise SystemExit("local-only: model downloads are off (unset REELLY_LOCAL_ONLY)")
    m = MODELS[key]
    dst = os.path.join(config.HOME, "models", m["file"])
    if os.path.exists(dst) and _sha256(dst) == m["sha256"]:
        print(f"[models] {key}: already installed ({dst})")
        return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part"
    print(f"[models] {key}: downloading {m['url']}")
    urllib.request.urlretrieve(m["url"], tmp)
    got = _sha256(tmp)
    if got != m["sha256"]:
        os.remove(tmp)
        raise SystemExit(f"[models] {key}: checksum mismatch ({got}); not installed")
    os.replace(tmp, dst)
    print(f"[models] {key}: installed {dst} ({m['license']})")
    return dst


def status():
    """[(key, path or None, description)] for `reelly setup` and `reelly models`."""
    return [(k, path(k), m["what"]) for k, m in MODELS.items()]
