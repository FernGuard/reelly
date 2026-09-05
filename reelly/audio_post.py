"""M4 audio post: S6 voice stem, ElevenLabs music bed, SFX cues, ducked mix.

Everything ships as separate stems (the reviewer's standing rule: music always
editable) and the final mix hits the platform loudness target.
"""
import hashlib
import json
import os
import subprocess
import threading
import time

import requests

from . import config, ledger, media

# finalize runs cuts on a thread pool; the ledger is a read-modify-write JSON
# file (same guard as direct.py) and the SFX cache is check-then-generate.
_LEDGER_LOCK = threading.Lock()
_SFX_LOCK = threading.Lock()

SFX_DIR = os.path.join(config.HOME, "sfx")
SFX_SPECS = {
    "whoosh": ("short cinematic whoosh transition, fast air swipe, clean tail, no music", 1.0),
    "riser": ("quick cinematic tension riser swell, one and a half seconds, no music", 1.5),
    "impact": ("soft deep cinematic impact thud, subtle and punchy, no music", 1.0),
}
EST_SFX = 0.03
EST_MUSIC = 0.06

# Genre beds, written as records a producer would actually make. "Clean corporate
# tech energy" is what stock music sounds like; naming a real genre, tempo and kit
# is what keeps a bed off the presentation-deck end of the spectrum.
BEDS = {
    "lofi": ("lo-fi hip-hop, dusty boom-bap drums around 85 BPM, warm Rhodes chords, "
             "tape saturation, vinyl crackle, round sub bass, unhurried and human, "
             "late-night studio feel"),
    "house": ("underground minimal house around 122 BPM, punchy analog drum machine, "
              "hypnotic rolling bassline, subtle filtered arp, restrained and modern, "
              "forward motion without hype"),
    "phonk": ("dark melodic phonk around 140 BPM, distorted 808 slides, cowbell rhythm, "
              "gritty low end, mounting tension that breaks open, attitude and swagger"),
}
# Steer away from the stock-library attractor the model otherwise falls into.
ANTI_STOCK = ("Not corporate, not a presentation soundtrack, not motivational stock "
              "music, no generic uplifting tech ambience, no cheesy plucks or claps.")


def _headers():
    return {"Authorization": f"Key {config.provider_key('fal-ai')}",
            "Content-Type": "application/json"}


def _find_audio_url(d):
    if isinstance(d, dict):
        for v in d.values():
            u = _find_audio_url(v)
            if u:
                return u
    elif isinstance(d, list):
        for v in d:
            u = _find_audio_url(v)
            if u:
                return u
    elif isinstance(d, str) and d.startswith("http") and d.split("?")[0].endswith(
            (".mp3", ".wav", ".m4a", ".ogg")):
        return d
    return None


# Appended to a rejected AUDIO prompt on the one retry. ElevenLabs' policy filter
# trips when "voice"/"speech" terms sit near product names in a sound prompt; the
# clause below tells the model to score instruments only, which is what a bed is.
INSTRUMENTAL_CLAUSE = (" Instrumental only: no voices, no vocals, no speech, no "
                       "singing, no lyrics, no spoken words, no vocal samples.")


class _ContentPolicy(RuntimeError):
    """FAL refused the prompt on content policy (not a transient failure)."""


def _is_content_policy(blob):
    """True when a FAL failure blob names a content-policy rejection."""
    s = (blob if isinstance(blob, str) else json.dumps(blob)).lower()
    return ("content_policy" in s or "content policy" in s
            or "flagged" in s or "safety" in s and "prompt" in s)


# ---------- resumable fal jobs: never pay twice for the same render ----------
#
# A fal submit is billed at queue time, not at collection. A timeout or a killed
# run used to mean re-submitting -- paying a second time to render a job that may
# already be COMPLETED on fal's side. We persist the queue handle (request_id +
# status/response urls) keyed by the exact (endpoint, payload, project) the
# moment it is submitted, and resume it before ever submitting again. The entry
# is cleared on success or on a terminal failure; a still-generating timeout
# keeps it, so the next run picks the job back up.
_PENDING = os.path.join(config.HOME, "fal_pending.json")
_PENDING_LOCK = threading.Lock()


def _job_key(endpoint, payload, project):
    blob = json.dumps([endpoint, payload, project], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()


def _pending_load():
    try:
        with open(_PENDING) as fh:
            return json.load(fh)
    except Exception:
        return {}


def _pending_save(reg):
    os.makedirs(config.HOME, exist_ok=True)
    tmp = _PENDING + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(reg, fh)
    os.replace(tmp, _PENDING)


def _pending_put(key, d):
    with _PENDING_LOCK:
        reg = _pending_load()
        reg[key] = {"request_id": d.get("request_id"),
                    "status_url": d.get("status_url"),
                    "response_url": d.get("response_url")}
        _pending_save(reg)


def _pending_peek(key):
    with _PENDING_LOCK:
        return _pending_load().get(key)


def _pending_pop(key):
    with _PENDING_LOCK:
        reg = _pending_load()
        if reg.pop(key, None) is not None:
            _pending_save(reg)


def _poll_and_collect(d, endpoint, tries, find):
    """Poll an already-submitted fal job to completion and return its media url.
    Raises _ContentPolicy on a policy failure and RuntimeError on any other
    failure or on a still-generating timeout (message kept stable so callers can
    tell a timeout, which is resumable, from a terminal failure)."""
    from . import runlog
    t0 = time.monotonic()
    for i in range(tries):
        time.sleep(2)
        if i and i % 30 == 0:
            # a video job can legitimately take many minutes; in a background
            # session that silence is indistinguishable from a hang. Once a
            # minute: say it is alive, and heartbeat the run log.
            mins = (time.monotonic() - t0) / 60
            print(f"         [fal] {endpoint} still generating after "
                  f"{mins:.0f}m (request_id={d.get('request_id')}; resumable)")
            runlog.beat(f"fal {endpoint} {mins:.0f}m")
        s = requests.get(d["status_url"], headers=_headers(), timeout=30).json()
        if s.get("status") == "COMPLETED":
            break
        if s.get("status") in ("FAILED", "ERROR"):
            if _is_content_policy(s):
                raise _ContentPolicy(f"fal {endpoint}: {s}")
            raise RuntimeError(f"fal {endpoint} failed: {s}")
    else:
        # Falling through used to fetch the response anyway, which surfaced a
        # still-generating job as "no media url" -- a paid render abandoned and
        # misreported as a model fault. Name the real cause and keep the id so
        # the result can still be collected (persisted by the caller).
        raise RuntimeError(
            f"fal {endpoint} still generating after {tries * 2}s; gave up. "
            f"request_id={d.get('request_id')} status_url={d.get('status_url')}")
    out = requests.get(d["response_url"], headers=_headers(), timeout=60).json()
    url = (find or _find_audio_url)(out)
    if not url:
        raise RuntimeError(f"no media url in fal response: {str(out)[:200]}")
    return url


def _fal_once(endpoint, payload, detail, project, service, find, tries):
    """One submit + poll, but RESUME first: if a request for this exact
    (endpoint, payload, project) is already persisted -- from a timeout or a
    killed run -- poll that job instead of paying to render it again. Raises
    _ContentPolicy when FAL rejects the prompt on policy grounds so the caller
    can decide whether an auto-fix is possible."""
    key = _job_key(endpoint, payload, project)
    saved = _pending_peek(key)
    if saved and saved.get("status_url"):
        print(f"         resuming fal {endpoint} request "
              f"{saved.get('request_id')} (already submitted; not paying again)")
        try:
            url = _poll_and_collect(saved, endpoint, tries, find)
            _pending_pop(key)
            return url
        except _ContentPolicy:
            _pending_pop(key)
            raise
        except RuntimeError as e:
            if "still generating" in str(e):
                raise                       # keep the entry; resume again later
            # The saved job is terminally gone (FAILED/expired): drop it and
            # submit fresh below.
            print(f"         saved fal request unusable ({e}); resubmitting")
            _pending_pop(key)
    r = requests.post(f"https://queue.fal.run/{endpoint}", headers=_headers(),
                      json=payload, timeout=60)
    if r.status_code in (400, 422) and _is_content_policy(r.text):
        raise _ContentPolicy(r.text)
    r.raise_for_status()
    d = r.json()
    _pending_put(key, d)          # persist BEFORE polling: a kill now is resumable
    try:
        url = _poll_and_collect(d, endpoint, tries, find)
    except _ContentPolicy:
        _pending_pop(key)         # a rejected prompt will never succeed on resume
        raise
    except RuntimeError as e:
        if "still generating" not in str(e):
            _pending_pop(key)     # a terminal failure won't resume; a timeout will
        raise
    _pending_pop(key)
    return url


def _log_wait(endpoint, service, project, seconds):
    """Append one line to the global FAL wait log (~/.reelly/fal_timings.jsonl).

    These waits are the invisible long poles of motion/sizzle/longform
    wall-clock: they never land in a project timings.json, so before this
    there was no data to rank them against the instrumented stages."""
    try:
        os.makedirs(config.HOME, exist_ok=True)
        with open(os.path.join(config.HOME, "fal_timings.jsonl"), "a") as f:
            f.write(json.dumps({"ts": time.time(), "endpoint": endpoint,
                                "service": service, "project": project,
                                "seconds": round(seconds, 1)}) + "\n")
    except OSError:
        pass  # instrumentation must never kill a render


def _fal(endpoint, payload, est, detail, project="", service="fal-audio",
         find=None, tries=150):
    """Queue-submit + poll one FAL endpoint. The single FAL video/audio client
    for the whole pipeline: `motion` reuses it with service="fal-video" and its
    own URL matcher rather than growing a second copy of this loop.

    On a content-policy rejection of an AUDIO prompt, retry ONCE with an
    instrumental-only clause appended (the paid-for failure: "voices" near a
    product term crashed with no retry) and re-raise with a clear message if it
    is still refused. Image/video prompts get no auto-fix: the instrumental
    clause is meaningless for them, so their rejection surfaces immediately.
    """
    with _LEDGER_LOCK:
        ledger.check(est)
    t0 = time.monotonic()
    try:
        url = _fal_once(endpoint, payload, detail, project, service, find, tries)
    except _ContentPolicy as e:
        key = ("prompt" if "prompt" in payload
               else "text" if "text" in payload else None)
        if not service.startswith("fal-audio") or key is None:
            raise RuntimeError(f"fal {endpoint} rejected the prompt on content "
                               f"policy and it cannot be auto-fixed: {e}")
        print("         FAL content policy rejected the sound prompt; "
              "retrying instrumental-only")
        retry = dict(payload)
        retry[key] = retry[key] + INSTRUMENTAL_CLAUSE
        try:
            url = _fal_once(endpoint, retry, detail, project, service, find, tries)
        except _ContentPolicy as e2:
            raise RuntimeError(
                f"fal {endpoint} still refused the audio prompt after an "
                f"instrumental-only retry; reword the sound prompt by hand: {e2}")
    _log_wait(endpoint, service, project, time.monotonic() - t0)
    with _LEDGER_LOCK:
        ledger.add(service, detail, est, project)
    return url


def _download(url, path):
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    return path


def sfx(name, project=""):
    """Cached generated SFX (whoosh/riser/impact); costs cents once, ever."""
    os.makedirs(SFX_DIR, exist_ok=True)
    p = os.path.join(SFX_DIR, f"{name}.mp3")
    # lock the check-then-generate: two parallel cuts must not both pay for
    # (and concurrently write) the same cached file
    with _SFX_LOCK:
        if os.path.exists(p):
            return p
        prompt, dur = SFX_SPECS[name]
        # /v2: the base endpoint defaults to a retired ElevenLabs model (probed)
        url = _fal("fal-ai/elevenlabs/sound-effects/v2",
                   {"text": prompt}, EST_SFX, f"sfx {name}", project)
        return _download(url, p)


def pick_bed(plan):
    """Choose a genre from what the cut actually does, not from one global default.

    A montage that keeps cutting wants to be driven; a reveal wants room for the
    moment to land; an explainer wants to stay out of the voice's way.
    """
    segs = len(plan.get("segments") or [])
    fmt = plan.get("format", "")
    jump = bool((plan.get("payoff") or {}).get("jump"))
    if fmt == "F5" or segs >= 3:      # build / montage: drive it
        return "phonk" if fmt == "F5" else "house"
    if jump:                          # reveal / payoff: let the moment land
        return "lofi"
    if fmt == "F8":                   # upbeat, but no single payoff to protect
        return "house"
    return "lofi"                     # calm explainer


# Music prefetch: the bed depends ONLY on the cut plan, so it can be queued at
# FAL the moment plans exist and download while segments encode — instead of
# each finalize worker sitting through the 10-13s queue+poll wait mid-chain.
# Registry maps out_mp3 path -> Future; check-then-act on (registry, file) is
# under _PREFETCH_LOCK so a prefetch and a later music() call can never both
# pay for the same bed.
_PREFETCH_LOCK = threading.Lock()
_PREFETCH = {}
_PREFETCH_POOL = None


def prefetch_music(plan, out_mp3, project=""):
    """Start generating the cut's music bed in the background.

    Returns a concurrent.futures.Future resolving to out_mp3. Idempotent per
    path: a second call (or a later music() call) joins the in-flight future
    rather than generating twice. When the file is already cached the future
    is already resolved.
    """
    import concurrent.futures
    global _PREFETCH_POOL
    with _PREFETCH_LOCK:
        fut = _PREFETCH.get(out_mp3)
        if fut is not None and not (fut.done() and fut.exception()):
            return fut
        if os.path.exists(out_mp3):
            fut = concurrent.futures.Future()
            fut.set_result(out_mp3)
            _PREFETCH[out_mp3] = fut
            return fut
        if _PREFETCH_POOL is None:
            _PREFETCH_POOL = concurrent.futures.ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="music-prefetch")
        fut = _PREFETCH_POOL.submit(_music_generate, plan, out_mp3, project)
        _PREFETCH[out_mp3] = fut
        return fut


def wait_for(out_path):
    """The in-flight (or resolved) prefetch Future for out_path, or None."""
    with _PREFETCH_LOCK:
        return _PREFETCH.get(out_path)


def music(plan, out_mp3, project=""):
    """Music bed generated to the cut's exact length (S4: never trim a vibe).

    Joins an in-flight prefetch for the same path when one exists (same cache
    path finalize constructs), falls back to generating here when finalize
    runs standalone and nothing was prefetched.
    """
    fut = wait_for(out_mp3)
    if fut is not None:
        try:
            return fut.result()
        except Exception as e:  # noqa: BLE001 — prefetch failed; retry inline
            print(f"         music prefetch failed ({e}); regenerating inline")
            with _PREFETCH_LOCK:
                if _PREFETCH.get(out_mp3) is fut:
                    del _PREFETCH[out_mp3]
    if os.path.exists(out_mp3):
        return out_mp3
    return _music_generate(plan, out_mp3, project)


def _music_generate(plan, out_mp3, project=""):
    """A bed for the cut: the kit's SELF-BUILDING library first ($0), FAL only
    when the library has nothing that fits (shared by music() and the
    prefetch pool). Every fresh generation is registered back into the kit,
    so the library warms up organically -- no beds are pre-generated."""
    from . import brandkit
    if os.path.exists(out_mp3):
        return out_mp3
    dur = max(10, int(plan["duration_s"]) + 2)
    bed = pick_bed(plan)
    # KIT FIRST: a registered bed with the same genre and enough length is
    # copied, not regenerated. Downstream trim/beat machinery is untouched:
    # beat_offset picks the phase and final_mix atrims to the cut, exactly as
    # it does for a fresh bed.
    kit_bed = brandkit.find_bed(bed, dur)
    if kit_bed:
        import shutil
        shutil.copyfile(kit_bed, out_mp3)
        print(f"         music: {bed} bed from kit ($0): "
              f"{os.path.basename(kit_bed)}")
        with _LEDGER_LOCK:
            ledger.add("brandkit-music", f"music {plan['id']} ({bed}, kit)",
                       0.0, project)
        return out_mp3
    about = (plan.get("title") or "").strip()
    prompt = (f"Instrumental {BEDS[bed]}. "
              f"Scores a short vertical video about: {about}. "
              f"Leave the mid-range sparse so a spoken voice sits clearly on top. "
              f"No vocals. Ends cleanly. {ANTI_STOCK}")
    print(f"         music: {bed} bed")
    url = _fal("fal-ai/elevenlabs/music",
               {"prompt": prompt, "music_length_ms": min(dur, 300) * 1000},
               EST_MUSIC, f"music {plan['id']}", project)
    _download(url, out_mp3)
    # Grow the library: the bed this cut just paid for serves every future
    # cut with the same genre and a shorter-or-equal length.
    try:
        real = float(media.probe(out_mp3)["format"]["duration"])
        if brandkit.register_bed(out_mp3, bed, real):
            print(f"         music: bed generated + registered into the kit "
                  f"library ({bed}, {real:.0f}s)")
    except Exception:   # noqa: BLE001 — registration must never fail a render
        pass
    return out_mp3


INTRO_S, OUTRO_S = 24.0, 20.0   # how long the bed plays in, and out
BED_LEVEL = 0.32                # under a voice that is already loudness-matched


def episode_bed(loud):
    """One genre for a whole episode, so it reads as a score and not a playlist."""
    peaks = (loud or {}).get("energy_peaks", [])
    return "house" if len(peaks) >= 6 else "lofi"


def longform_music(bed, seconds, out_mp3, role, project=""):
    """A distinct piece per role. Nothing is looped: a seam is audible over an hour."""
    if os.path.exists(out_mp3):
        return out_mp3
    opens = role == "intro"
    prompt = (f"Instrumental {BEDS[bed]}. "
              + ("Opens a long episode: sets the room and invites the viewer in, "
                 "then gets out of the way." if opens else
                 "Closes a long episode: resolves and lands, no fade to nothing.")
              + " Leave the mid-range sparse so a spoken voice sits clearly on top. "
              f"No vocals. Ends cleanly. {ANTI_STOCK}")
    print(f"[long ] music: {bed} bed ({role})")
    url = _fal("fal-ai/elevenlabs/music",
               {"prompt": prompt, "music_length_ms": min(max(10, int(seconds) + 4), 300) * 1000},
               EST_MUSIC, f"music longform {role}", project)
    return _download(url, out_mp3)


def score_longform(voice_wav, intro_mp3, outro_mp3, total_s, out_wav,
                   intro_s=INTRO_S, outro_s=OUTRO_S, level=BED_LEVEL):
    """Bed the way in and the way out, ducked under the voice; silence between.

    The full edit already collapses dead air, so a long-form cut has no gaps to
    fill (measured: zero silences over 2s at any threshold). Music laid wall to
    wall would spend the whole episode fighting the talking, so it plays only
    where there is nothing to fight. Sidechain, not a fixed level: the bed backs
    off under a voice and comes up when the voice stops.
    """
    outro_at = max(0.0, total_s - outro_s)
    d = int(outro_at * 1000)
    # Lay the beds onto an explicit bed of silence the length of the episode.
    # amix:duration=first then pins the result to that length, and each piece
    # sits where its adelay puts it. (Letting amix work it out from the pieces
    # alone drifted the length and dropped the outro entirely.)
    fc = (
        f"[1:a]atrim=0:{intro_s},asetpts=N/SR/TB,aresample=48000,"
        f"afade=t=out:st={max(0.0, intro_s - 5):.2f}:d=5,volume={level}[intro];"
        f"[2:a]atrim=0:{outro_s},asetpts=N/SR/TB,aresample=48000,"
        f"afade=t=in:st=0:d=4,volume={level},adelay={d}|{d}[outro];"
        f"[3:a][intro][outro]amix=inputs=3:duration=first:normalize=0[bed];"
        f"[bed][0:a]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=600[duck];"
        f"[0:a][duck]amix=inputs=2:duration=first:normalize=0[mix]"
    )
    subprocess.run([config.FFMPEG, "-y", "-v", "error",
                    "-i", voice_wav, "-i", intro_mp3, "-i", outro_mp3,
                    "-f", "lavfi", "-t", f"{total_s:.3f}",
                    "-i", "anullsrc=r=48000:cl=stereo",
                    "-filter_complex", fc, "-map", "[mix]",
                    "-ar", "48000", out_wav], check=True)
    return out_wav


def voice_stem(src_video, out_wav):
    """S6 chain as a stem: de-click, de-ess, highpass; leveled for mixing."""
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", src_video, "-vn",
                    "-af", "adeclick,deesser=i=0.4,highpass=f=70,"
                           "loudnorm=I=-15:TP=-2:LRA=9",
                    "-ar", "48000", out_wav], check=True)
    return out_wav


def sfx_events(plan):
    """(time_s, sfx_name, gain) cues per playbook S3: sparse, earned."""
    events = [(0.05, "impact", 0.7)]  # under the hook text (S3)
    payoff = plan.get("payoff") or {}
    if payoff.get("jump") and payoff.get("local_t"):
        t = float(payoff["local_t"])
        events.append((max(0.0, t - 1.3), "riser", 0.5))
        events.append((max(0.0, t - 0.12), "whoosh", 0.8))
    return events


def beat_offset(music_path, target_t):
    """Trim offset so a musical beat lands on target_t (S5: beat-aligned).

    Onset flux -> autocorrelation tempo (60-180 BPM) -> comb phase fit.
    Returns seconds to trim from the bed's start; 0.0 when unsure.
    """
    import numpy as np
    from scipy import signal
    r = subprocess.run([config.FFMPEG, "-v", "error", "-i", music_path,
                        "-ac", "1", "-ar", "22050", "-f", "f32le", "-"],
                       capture_output=True)
    x = np.frombuffer(r.stdout, dtype=np.float32)
    if len(x) < 22050 * 4:
        return 0.0
    _, _, Z = signal.stft(x, 22050, nperseg=1024, noverlap=512)
    mag = np.abs(Z)
    flux = np.maximum(mag[:, 1:] - mag[:, :-1], 0).sum(axis=0)
    hop = 512 / 22050
    f0 = flux - flux.mean()
    ac = np.correlate(f0, f0, "full")[len(f0) - 1:]
    lo, hi = int((60 / 180) / hop), int((60 / 60) / hop)
    if hi >= len(ac) or lo >= hi:
        return 0.0
    period = (lo + int(np.argmax(ac[lo:hi]))) * hop
    nb = max(1, int((len(flux) * hop) / period))
    best_phi, best_score = 0.0, -1.0
    for phi in np.arange(0.0, period, hop):
        idx = ((phi + np.arange(nb) * period) / hop).astype(int)
        idx = idx[idx < len(flux)]
        score = float(flux[idx].sum())
        if score > best_score:
            best_score, best_phi = score, phi
    return float((best_phi - target_t) % period)


def final_mix(voice_wav, music_mp3, events, dur, out_wav, project="",
              music_offset=0.0, pad_to=None):
    """Voice + [sidechain-ducked music] + placed SFX -> -14 LUFS mix.

    music_mp3=None builds the CLEAN mix (voice + SFX only) for platforms
    where trending in-app audio replaces a generated bed (playbook P5).

    pad_to (designed endings): the deliverable video runs PAST the voice --
    an appended brand outro -- so the voice stem is padded with silence to
    that full length. amix(duration=first) then carries the mix across the
    outro and the sidechain-ducked bed opens back up under it: the music
    continues, the voice never does.
    """
    inputs = [config.FFMPEG, "-y", "-v", "error", "-i", voice_wav]
    if music_mp3:
        inputs += ["-i", music_mp3]
    sfx_paths = []
    for t, name, gain in events:
        p = sfx(name, project)
        inputs += ["-i", p]
        sfx_paths.append((t, gain))
    base = 2 if music_mp3 else 1
    fc = []
    vmix = vsc = "[0:a]"
    if pad_to:
        if music_mp3:
            fc.append(f"[0:a]apad=whole_dur={pad_to:.3f},asplit=2[vmix][vsc]")
            vmix, vsc = "[vmix]", "[vsc]"
        else:
            fc.append(f"[0:a]apad=whole_dur={pad_to:.3f}[vmix]")
            vmix = "[vmix]"
    mix_ins = vmix
    if music_mp3:
        o = max(0.0, music_offset)
        fc += [f"[1:a]atrim={o:.3f}:{o + dur:.3f},asetpts=PTS-STARTPTS,volume=0.4[m]",
               f"[m]{vsc}sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[md]"]
        mix_ins += "[md]"
    for i, (t, gain) in enumerate(sfx_paths):
        fc.append(f"[{base + i}:a]volume={gain},adelay={int(t * 1000)}|{int(t * 1000)}[s{i}]")
        mix_ins += f"[s{i}]"
    n = (2 if music_mp3 else 1) + len(sfx_paths)
    premix = out_wav + ".premix.wav"
    if n > 1:
        fc.append(f"{mix_ins}amix=inputs={n}:duration=first:normalize=0[out]")
    else:
        fc.append(f"{vmix}anull[out]")
    inputs += ["-filter_complex", ";".join(fc), "-map", "[out]",
               "-ar", "48000", premix]
    subprocess.run(inputs, check=True)
    _loudnorm_2pass(premix, out_wav)
    os.remove(premix)
    return out_wav


def _loudnorm_2pass(src, dst, I=-14, TP=-2.0, LRA=11):
    """Measure, then normalize with measured values: single-pass loudnorm
    undershoots on dynamic content (judge caught -16.0 on a -14 target).
    TP -2.0 leaves headroom for AAC inter-sample overshoot (also judge-caught:
    a -1.5 wav encoded to -0.5 dBTP)."""
    r = subprocess.run([config.FFMPEG, "-i", src,
                        "-af", f"loudnorm=I={I}:TP={TP}:LRA={LRA}:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True)
    tail = r.stderr[r.stderr.rfind("{"):r.stderr.rfind("}") + 1]
    m = json.loads(tail)
    af = (f"loudnorm=I={I}:TP={TP}:LRA={LRA}:"
          f"measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
          f"offset={m['target_offset']}:linear=true")
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", src,
                    "-af", af, "-ar", "48000", dst], check=True)
    return dst


def true_peak(path):
    """Measured true peak of a file's audio, in dBFS."""
    import re
    r = subprocess.run([config.FFMPEG, "-hide_banner", "-nostats", "-i", path,
                        "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    peaks = re.findall(r"Peak:\s+(-?\d+\.?\d*)\s+dBFS", r.stderr)
    return float(peaks[-1]) if peaks else None


# How far under the judge's true-peak gate the correction aims. AAC re-encode
# adds inter-sample overshoot (measured up to +3.6 dB on dense low end), so the
# enforced ceiling sits a fixed margin below judge.TP_MAX and is verified after.
TP_SAFETY = 0.6


def enforce_true_peak(path, ceiling=None, margin=0.3, source_audio=None):
    """Guarantee a delivered file sits under the true-peak ceiling.

    The ceiling is derived from the judge (judge.TP_MAX) so the two never drift:
    whatever peak the judge fails a file for, the correction stays a margin under
    it. A caller may still pass an explicit stricter ceiling.

    Normalising the MIX is not enough: the AAC encode at mux time adds
    inter-sample overshoot, and on dense low-end content that was measured at
    +3.6 dB — a mix correctly normalised to -2.0 dBFS arrived at +1.6 dBFS in
    the mp4. Nothing upstream can predict that reliably, so the only honest fix
    is to measure what was actually written and correct it if it is over.

    Re-encodes with the exact attenuation needed, video stream copied.
    """
    if ceiling is None:
        from . import judge
        ceiling = judge.TP_MAX - TP_SAFETY
    first = tp = true_peak(path)
    if tp is None or tp <= ceiling:
        return tp
    # Correct from the ORIGINAL audio every time, never from the last attempt.
    # Re-encoding an already-encoded file adds fresh overshoot on each pass, so
    # iterating on the mp4 fights itself: +1.6 went to -0.6 and then stuck there
    # while the applied gain and the generation loss cancelled out. Going back
    # to the pristine wav means each attempt is one encode deep, and the total
    # correction accumulates instead of being re-lost.
    # LIMIT the peaks, do not turn the whole mix down. Attenuating by the
    # overshoot drops integrated loudness with it: a -3.6 dB correction took a
    # file to -3.7 dBTP and would have pushed it under the -15.5 LUFS gate,
    # trading one failed gate for another. A limiter touches only the peaks and
    # leaves the loudness where loudnorm put it.
    #
    # The limiter runs on the SOURCE wav, then that is encoded once. The encoder
    # still adds its overshoot, so the limiter ceiling is set below the target by
    # however much this specific file was measured to overshoot, and verified.
    # Converge from the gentle side. Setting the limiter straight to
    # (ceiling - measured overshoot) squashed a file to -5.2 dBTP and dragged
    # integrated loudness to the edge of the -15.5 LUFS gate: correct on one
    # gate by breaking another. Start just under the ceiling and tighten only
    # as far as the measurements actually demand.
    limit_db = ceiling - margin
    for _ in range(4):
        tmp = path + ".tp.mp4"
        af = f"alimiter=limit={10 ** (limit_db / 20):.4f}:level=disabled"
        cmd = [config.FFMPEG, "-y", "-v", "error", "-i", path]
        if source_audio:
            cmd += ["-i", source_audio, "-map", "0:v", "-map", "1:a"]
        cmd += ["-c:v", "copy", "-af", af, "-c:a", "aac", "-b:a", "192k",
                "-shortest", tmp]
        subprocess.run(cmd, check=True)
        os.replace(tmp, path)
        tp = true_peak(path)
        if tp is None or tp <= ceiling:
            break
        limit_db -= (tp - ceiling) + margin
    print(f"         true peak {first:+.1f} -> {tp:+.1f} dBFS (ceiling {ceiling})")
    return tp


def integrated_loudness(path):
    """Measured integrated loudness of a file's audio, in LUFS."""
    import re
    r = subprocess.run([config.FFMPEG, "-hide_banner", "-nostats", "-i", path,
                        "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True)
    vals = re.findall(r"^\s+I:\s+(-?\d+\.?\d*)\s+LUFS", r.stderr, re.M)
    return float(vals[-1]) if vals else None


def enforce_loudness(path, lo=None, hi=None, target=None, source_audio=None):
    """Keep a delivered file inside the loudness window.

    The window and its centre come from the judge (judge.LUFS_LO/LUFS_HI) so a
    freshly composited file is corrected to exactly the band the judge will
    grade it against — the two cannot drift apart (the paid-for failure was
    enforcement landing ~1 dB below the judge's window on quiet bases). The
    overlay pass mixes SFX in without re-normalising, which pushed two files to
    -12.3 LUFS against a -12.5 ceiling. Correcting loudness can break the
    true-peak gate, so the peak guard runs again afterwards: the two gates are
    enforced together or not at all.
    """
    if lo is None or hi is None or target is None:
        from . import judge
        lo = judge.LUFS_LO if lo is None else lo
        hi = judge.LUFS_HI if hi is None else hi
        target = (judge.LUFS_LO + judge.LUFS_HI) / 2 if target is None else target
    i = integrated_loudness(path)
    if i is None or lo <= i <= hi:
        return i
    gain = target - i
    tmp = path + ".ln.mp4"
    cmd = [config.FFMPEG, "-y", "-v", "error", "-i", path]
    if source_audio:
        cmd += ["-i", source_audio, "-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "copy", "-af", f"volume={gain:.2f}dB", "-c:a", "aac",
            "-b:a", "192k", "-shortest", tmp]
    subprocess.run(cmd, check=True)
    os.replace(tmp, path)
    after = integrated_loudness(path)
    print(f"         loudness {i:.1f} -> {after:.1f} LUFS (window {lo}..{hi})")
    enforce_true_peak(path)
    return after
