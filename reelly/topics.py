"""UNDERSTAND: TF-IDF topic segmentation over the transcript (torch-free).

Finds where the speaker changes subject; those boundaries are natural clip
edges. Ported from the proven seed (story_clips_tx analyze mode).
"""


def sentences(words):
    sents, cur = [], []
    for w in words:
        cur.append(w)
        if w["t"][-1:] in ".?!":
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)
    return [{"text": " ".join(x["t"] for x in s), "s": s[0]["s"], "e": s[-1]["e"]}
            for s in sents if s]


def topic_clips(sents, W=3, k=0.6, min_s=8, max_s=90, max_gap=10.0):
    """Gap-aware topic segmentation.

    Sparse recordings (build sessions) have long silent stretches that are
    obvious boundaries TF-IDF cannot see, so first split on speech gaps
    longer than max_gap, then run TF-IDF within each talky block.
    """
    blocks, cur = [], []
    for s in sents:
        if cur and s["s"] - cur[-1]["e"] > max_gap:
            blocks.append(cur)
            cur = []
        cur.append(s)
    if cur:
        blocks.append(cur)
    out = []
    for b in blocks:
        out.extend(_tfidf_clips(b, W, k, min_s, max_s))
    return out


def _tfidf_clips(sents, W=3, k=0.6, min_s=8, max_s=90):
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer
    texts = [s["text"] for s in sents]
    if len(texts) < 2 * W + 2:
        return [{"s": sents[0]["s"], "e": sents[-1]["e"], "text": " ".join(texts)}] if sents else []
    vec = TfidfVectorizer(stop_words="english").fit(texts)

    def blk(a, b):
        return vec.transform([" ".join(texts[a:b])])

    sims = []
    for g in range(len(texts) - 1):
        L = blk(max(0, g - W + 1), g + 1)
        R = blk(g + 1, g + 1 + W)
        num = (L.multiply(R)).sum()
        den = (np.sqrt(L.multiply(L).sum()) * np.sqrt(R.multiply(R).sum())) or 1.0
        sims.append(num / den)
    sims = np.array(sims)
    thr = sims.mean() - k * sims.std()
    bounds = [g for g in range(1, len(sims) - 1)
              if sims[g] < thr and sims[g] <= sims[g - 1] and sims[g] <= sims[g + 1]]
    clips, start = [], 0
    for b in sorted(set(bounds)) + [len(sents) - 1]:
        seg = sents[start:b + 1]
        if seg:
            c = {"s": seg[0]["s"], "e": seg[-1]["e"], "text": " ".join(x["text"] for x in seg)}
            if clips and (c["e"] - c["s"]) < min_s and (clips[-1]["e"] - clips[-1]["s"]) < max_s:
                clips[-1]["e"] = c["e"]
                clips[-1]["text"] += " " + c["text"]
            else:
                clips.append(c)
        start = b + 1
    return clips
