"""Reelly CLI.

  reelly setup [--json]
  reelly analyze <video> [--facecam F] [--name N] [--out DIR] [--skip-visual]
                 [--force] [--crop W:H:X:Y]
  reelly budget
  reelly budget sprint <cap> [--days N] [--reason TEXT]
  reelly budget endsprint
"""
import argparse

from . import ledger


class _CutGfx:
    """Per-cut graphics pipeline for `reelly cut`.

    finalize's on_cut seam hands each finished cut here; its overlays
    (autoplan + apply for THAT cut only) run on a 1-wide pool so they overlap
    the cuts finalize is still rendering. One worker on purpose: autoplan
    with cut_id is a read-modify-write of edl/overlay_specs.json, so per-cut
    gfx jobs must serialise against each other (they still overlap finalize).
    Failures are isolated per cut and reported together in finish(), matching
    the old full-phase overlays.apply behaviour.
    """

    def __init__(self, project, product, autoplan_needed, tag=None):
        from concurrent.futures import ThreadPoolExecutor
        self.project, self.product, self.tag = project, product, tag
        self.autoplan_needed = autoplan_needed
        self.pool = ThreadPoolExecutor(max_workers=1,
                                       thread_name_prefix="cut-gfx")
        self.futs = []   # [(cut_id, Future)] in completion-submission order

    def on_cut(self, plan, made):
        """finalize callback: runs on the finalize worker thread, so it only
        queues the job and returns."""
        self.futs.append((plan["id"], self.pool.submit(self._one, plan["id"])))

    def _one(self, cid):
        from . import overlays
        if self.autoplan_needed:
            overlays.autoplan(self.project, product=self.product, cut_id=cid,
                              tag=self.tag)
        overlays.apply(self.project, cut_id=cid, tag=self.tag)

    def finish(self):
        """Barrier: wait for every queued gfx job; raise like apply() did."""
        failed = []
        for cid, fu in self.futs:
            try:
                fu.result()
            except Exception as e:  # noqa: BLE001 — isolate per cut
                print(f"[overlays] {cid} FAILED: {e}")
                failed.append(cid)
        self.pool.shutdown()
        if failed:
            raise RuntimeError("overlays failed on: " + ", ".join(failed))


def main():
    ap = argparse.ArgumentParser(prog="reelly")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="full analysis bundle for a recording")
    a.add_argument("video")
    a.add_argument("--facecam", help="separate facecam file to sync into the session")
    a.add_argument("--name", help="project name (default: video basename)")
    a.add_argument("--out", help="projects root (default: config.DEFAULT_PROJECTS)")
    a.add_argument("--skip-visual", action="store_true", help="skip the Gemini pass")
    a.add_argument("--force", action="store_true", help="ignore cached artifacts")
    a.add_argument("--crop", help="W:H:X:Y crop to hide a baked-in facecam corner")

    d = sub.add_parser("direct", help="editor brain: cut plans from an analysis bundle")
    d.add_argument("project", help="project name or path")
    d.add_argument("--no-ai", action="store_true", help="heuristic refinement only")
    d.add_argument("--max-cuts", type=int, default=8)
    d.add_argument("--brain", choices=["gemini", "gpt"], default="gpt",
                   help="language-model backend for edit refinement")
    d.add_argument("--tag", help="suffix for plan/preview files (A/B experiments)")

    c = sub.add_parser("cut", help="ONE PASS: plans -> fully treated videos + REVIEW.md "
                                   "(the normal way to make cuts)")
    c.add_argument("project", help="project name or path")
    c.add_argument("--replan", action="store_true",
                   help="re-run the editor brain even if cut plans already exist")
    c.add_argument("--max-cuts", type=int, default=8)
    c.add_argument("--brain", choices=["gemini", "gpt"], default="gpt")
    c.add_argument("--tag", help="suffix for plan/deliverable files (A/B experiments)")
    c.add_argument("--product", default="video",
                   choices=["video", "story", "games", "adventure"])
    c.add_argument("--for", dest="targets_for", metavar="PLATFORMS",
                   help="comma list (tiktok,reels,shorts,youtube,x,master)")
    c.add_argument("--account", help="publishing account profile "
                                     "(default: project delivery.json, else creator)")
    c.add_argument("--variants", metavar="VARIANTS",
                   help="comma list of deliverable variants "
                        "(plain,gfx,trending,trending_gfx); "
                        "default: delivery.json, else gfx")
    c.add_argument("--skip-judge", action="store_true", help="skip the QC gates")
    c.add_argument("--skip-overlays", action="store_true",
                   help="skip the graphics layer even if overlay_specs.json exists")

    p = sub.add_parser("preview", help="[debug] rough render, no music/SFX/end tag. "
                                       "NOT the review artifact: use `cut`")
    p.add_argument("project", help="project name or path")
    p.add_argument("--cut", help="render one cut id only (e.g. cut_03)")
    p.add_argument("--tag", help="render the tagged plan set (A/B experiments)")

    f = sub.add_parser("finalize", help="post-ready render: music, SFX, styled captions, attribution")
    f.add_argument("project", help="project name or path")
    f.add_argument("--cut", help="finalize one cut id only")
    f.add_argument("--tag", help="use the tagged plan set")
    f.add_argument("--product", default="video",
                   choices=["video", "story", "games", "adventure"])
    f.add_argument("--for", dest="targets_for", metavar="PLATFORMS",
                   help="comma list (tiktok,reels,shorts,youtube,x,threads,master); "
                        "default: project delivery.json, else tiktok,reels,shorts,threads,x")
    f.add_argument("--force", action="store_true",
                   help="ignore the cached raw cut and re-render segments")
    f.add_argument("--account", help="publishing account profile")
    f.add_argument("--variants", metavar="VARIANTS",
                   help="comma list (plain,gfx,trending,trending_gfx)")

    bi = sub.add_parser("burnin", help="karaoke captions + beds on a finished master")
    bi.add_argument("video")
    bi.add_argument("--project", help="project name, for the music beds")
    bi.add_argument("--out")
    bi.add_argument("--no-music", action="store_true")
    bi.add_argument("--cut-pauses", action="store_true",
                    help="collapse pauses longer than --max-pause")
    bi.add_argument("--max-pause", type=float, default=0.9)

    lf = sub.add_parser("longform", help="full engagement edit: 16:9, chapters, SRT")
    lf.add_argument("project", help="project name or path")
    lf.add_argument("--product", default="video",
                    choices=["video", "story", "games", "adventure"])
    lf.add_argument("--no-music", action="store_true",
                    help="voice only: skip the intro/outro bed")

    ov = sub.add_parser("overlays", help="graphics overlays + entrance SFX onto finished cuts")
    ov.add_argument("project")
    ov.add_argument("--cut", help="one cut id only")
    ov.add_argument("--src", help="source dir (default deliverables/final)")
    ov.add_argument("--tag", default="",
                    help="plan-set tag: read edl/cut_plans_<tag>.json and "
                         "deliverables/final_<tag>/ (matches cut/finalize --tag)")
    ov.add_argument("--auto", action="store_true",
                    help="decide the marks and place them from frame content first")
    ov.add_argument("--label", help="what the brand lower-third names (default: project)")
    ov.add_argument("--kicker", default="MADE WITH")
    ov.add_argument("--no-meme", action="store_true", help="brand register only")
    ov.add_argument("--logo", help="brand logo PNG for the badge (beats typeset lettering)")
    ov.add_argument("--cta", help="fallback call to action when a plan has none")
    ov.add_argument("--product", default="adventure",
                    choices=["video", "story", "games", "adventure"],
                    help="which product profile the end card uses")

    mo = sub.add_parser("motion", help="single image -> finished vertical post "
                                       "(brain copy + text-free i2v + campaign type system)")
    mo.add_argument("image", help="source still (keyart, screenshot, poster)")
    mo.add_argument("--message",
                    help="what the post must communicate (the brain authors the "
                         "hook/payoff/CTA from this under the copy contract)")
    mo.add_argument("--campaign", default=None,
                    help="campaign spec name (~/.reelly/campaigns/<name>.json: "
                         "lettering asset, palette, CTA, archetypes)")
    mo.add_argument("--name", help="project name (default: motion-<image>)")
    mo.add_argument("--hero", action="store_true",
                    help="hero-tier render (M2: draft is the default review artifact)")
    mo.add_argument("--real-art", action="store_true",
                    help="posts about EXISTING games / real product UI: animate "
                         "the real still by camera only, invent no character "
                         "(also settable per-campaign with \"real_art\": true)")
    mo.add_argument("--copy",
                    help="hand-author the copy: path to a JSON file with any of "
                         "hook/payoff/cta/caption. Skips the sense gate for the "
                         "supplied fields and records the plan as hand-authored")
    mo.add_argument("--video-model", choices=["h3max", "seedance", "minimax", "grok"],
                    default="h3max",
                    help="video model. h3max (H3 Max image-to-video) is the DEFAULT -- "
                         "the FAST path: animates one composed keyframe at 768P in ~20s "
                         "for ~$0.60/15s (~20x faster + cheaper than base H3), but no "
                         "LoRA or audio refs. minimax (H3 reference-to-video) is the "
                         "multi-reference/LoRA/voice path (slow, ~11min at 15s). seedance "
                         "is strongest on stylised art and camera moves over existing "
                         "artwork, and renders humans badly. grok is single-frame and the "
                         "only one that takes an explicit aspect_ratio.")
    mo.add_argument("--image-model", choices=["nano-banana", "grok"],
                    default="nano-banana",
                    help="image model for lettering plates and style specimens.")
    mo.add_argument("--speech", action="store_true",
                    help="the generated clip carries intended DIALOGUE: keep its "
                         "native H3 audio up front and duck the music bed under "
                         "it. Default off -- native audio rides as foley/ambience "
                         "with the bed steady under it (foley + SFX + bed).")
    mo.add_argument("--trending", action="store_false", dest="music",
                    help="TRENDING-audio mode (opt-in, like --speech): ship native "
                         "foley + SFX only, NO generated music bed, so the "
                         "account's trending sound rides on top. Default ships the "
                         "self-contained mix (native foley + SFX + bed); the "
                         "native audio is never stripped either way.")
    mo.add_argument("--brain", choices=["gemini", "gpt"], default="gpt")
    mo.add_argument("--skip-judge", action="store_true")
    mo.add_argument("--seconds", type=int, default=None,
                    help="force TOTAL video length in seconds (rescales the "
                         "planned shots to sum to this; default: the brain plans "
                         "~8s). Feeds both the prompt and the H3 `duration`.")

    sz = sub.add_parser("sizzle", help="pool of finished clips -> product sizzle reel "
                                       "(vision clearance + shot-list brain + house grammar)")
    sz.add_argument("product", choices=["video", "story", "games", "adventure"])
    sz.add_argument("clips", nargs="+",
                    help="clip files, globs, or directories to draw the pool from")
    sz.add_argument("--out", required=True, help="output mp4")
    sz.add_argument("--seconds", type=float, default=30.0)
    sz.add_argument("--size", default=None,
                    help="frame size, WxH. Sizzle is LANDSCAPE-FIRST: the "
                         "default is 1920x1080 (store pages, decks, showcase). "
                         "Pass --social/--portrait for the 1080x1920 feed cut, "
                         "or set an explicit WxH here to override both.")
    sz.add_argument("--social", "--portrait", dest="social", action="store_true",
                    help="opt in to the PORTRAIT 1080x1920 social/feed variant. "
                         "Landscape 1920x1080 stays the default; this flag (or an "
                         "explicit --size) is the only way to get vertical.")
    sz.add_argument("--brain", choices=["gemini", "gpt"], default="gpt")
    sz.add_argument("--work", help="work dir for survey/plan/picture (default: <out>/_work)")
    sz.add_argument("--crop", metavar="[SUBSTR=]W:H:X:Y", action="append",
                    help="crop sources before BOTH the clearance pass and the "
                         "render, e.g. to lift a player out of app chrome (the "
                         "vision gate then judges the cropped frame). Bare "
                         "W:H:X:Y applies to every source; SUBSTR=W:H:X:Y only "
                         "to paths containing SUBSTR. Repeatable.")
    sz.add_argument("--allow-weapons", action="store_true",
                    help="scope OFF the no-guns/no-blood rule for THIS run. It "
                         "is a reach penalty on the vertical feeds, so a reel "
                         "for a store page or a deck does not pay it. Recorded "
                         "in the plan; writes NOT-FOR-SOCIAL.md next to the "
                         "output. Never affects the retired-branding gate.")
    sz.add_argument("--window", type=float, default=120.0,
                    help="max seconds one pool entry may cover (default 120). "
                         "Lower it to split long recordings into more "
                         "candidates when the pool is thin.")
    sz.add_argument("--script", metavar="PLAN.json",
                    help="render a HUMAN-AUTHORED shot list verbatim: skips the "
                         "survey, the clearance gate and the brain. The plan "
                         "names exact files, timecodes and on-screen copy.")
    sz.add_argument("--no-cinecard", action="store_true",
                    help="skip the generated cinematic bed for the brand beats "
                         "(they fall back to a blurred still from the film)")
    sz.add_argument("--replan", action="store_true",
                    help="re-author the shot list + copy even if a plan.json "
                         "already exists (default: REUSE it, so a text/label "
                         "re-render stays on the cheap deterministic layers "
                         "instead of regenerating the title beat)")
    sz.add_argument("--survey-only", action="store_true",
                    help="run the vision clearance pass and stop (what is usable, "
                         "and why each drop was dropped)")

    rv = sub.add_parser("reveal", help="ordered cards -> gacha-style reveal "
                                       "(escalating size + fast transitions + cosmic bg)")
    rv.add_argument("cards", nargs="+",
                    help="card images in reveal order (last = the climax)")
    rv.add_argument("--out", required=True, help="output mp4")
    rv.add_argument("--bg", help="cosmic background mp4 (generated if omitted)")
    rv.add_argument("--project", default="", help="project label for spend tracking")

    h = sub.add_parser("handoff", help="DaVinci Resolve handoff: FCPXML + SRT + markers")
    h.add_argument("project", help="project name or path")
    h.add_argument("--tag", help="use the tagged plan set")

    nw = sub.add_parser("newsletter", help="Substack note drafted from the transcript")
    nw.add_argument("project", help="project name or path")
    nw.add_argument("--tag", help="plan set context")
    nw.add_argument("--product", default="video",
                    choices=["video", "story", "games", "adventure"])

    dl = sub.add_parser("deliver", help="post-verdict stage: KEEPs only, renumbered, "
                                        "account variants, mapping written")
    dl.add_argument("project", help="project name or path")
    dl.add_argument("--tag", help="use the tagged plan set")
    dl.add_argument("--product", default="video",
                    choices=["video", "story", "games", "adventure"])
    dl.add_argument("--account", help="publishing account profile")
    dl.add_argument("--variants", metavar="VARIANTS",
                    help="comma list (plain,gfx,trending,trending_gfx)")
    dl.add_argument("--for", dest="targets_for", metavar="PLATFORMS",
                    help="comma list of delivery targets")

    ln = sub.add_parser("learn", help="verdicts + metrics -> outlier scores + playbook proposals")
    ln.add_argument("--project", help="project name or path (for plan evidence)")
    ln.add_argument("--metrics", help="metrics.json fetched from analytics export")
    ln.add_argument("--tag", help="plan set the clips came from")

    cr = sub.add_parser("clear", help="record voice clearance for a diarized "
                                      "speaker (default-deny: uncleared "
                                      "speakers block planning and QC)")
    cr.add_argument("project", help="project name or path")
    cr.add_argument("--speaker", required=True, metavar="ID",
                    help="diarized speaker id (see analysis/speaker_turns.json)")
    cg = cr.add_mutually_exclusive_group(required=True)
    cg.add_argument("--cleared", action="store_true",
                    help="this voice may ship (requires --by)")
    cg.add_argument("--uncleared", action="store_true",
                    help="this voice must not ship (the default state)")
    cr.add_argument("--by", help="who made the call (required with --cleared)")
    cr.add_argument("--note", help="context, e.g. 'release on file'")

    j = sub.add_parser("judge", help="QC gates on every deliverable")
    j.add_argument("project", help="project name or path")
    j.add_argument("--tag", help="check the tagged final set")
    j.add_argument("--visual", action="store_true",
                   help="perceptual pass: composite each cut join, Gemini-review")

    b = sub.add_parser("budget", help="show spend, or manage sprint mode")
    bsub = b.add_subparsers(dest="bcmd")
    s = bsub.add_parser("sprint", help="temporarily raise the cap")
    s.add_argument("cap", type=float)
    s.add_argument("--days", type=int, default=14)
    s.add_argument("--reason", default="")
    bsub.add_parser("endsprint", help="end the sprint, back to the default cap")

    sub.add_parser("runs", help="active/stuck/crashed background runs "
                                "(heartbeats from every command)")

    su = sub.add_parser("setup", help="check binaries and API keys "
                                      "(never prints key values)")
    su.add_argument("--json", action="store_true",
                    help="machine-readable check results")

    args = ap.parse_args()
    from . import runlog
    if args.cmd == "runs":
        import sys
        sys.exit(1 if runlog.report() else 0)
    if args.cmd == "setup":
        import sys
        from . import config
        sys.exit(config.doctor(as_json=args.json))
    if args.cmd != "budget":
        # every real command heartbeats: `reelly runs` then answers "which of
        # my parallel sessions is stuck, and on what stage" in one glance
        runlog.start(args.cmd, getattr(args, "project", "")
                     or getattr(args, "name", "") or "")
    if args.cmd == "analyze":
        from . import analyze
        analyze.run(args.video, facecam=args.facecam, name=args.name,
                    out_root=args.out, skip_visual=args.skip_visual,
                    force=args.force, crop=args.crop)
    elif args.cmd == "direct":
        from . import direct
        direct.run(args.project, ai=not args.no_ai, max_cuts=args.max_cuts,
                   brain=args.brain, tag=args.tag)
    elif args.cmd == "cut":
        # One pass, one review. Plans, then fully treated renders, then QC.
        import json as _json
        import os
        from . import accounts, audio_post, direct, finalize, judge, products
        root = direct.resolve_project(args.project)
        profile = accounts.for_project(root, args.account)
        wanted = accounts.variants_for(root, profile, args.variants)
        sfx = f"_{args.tag}" if args.tag else ""
        plans_p = os.path.join(root, "edl", f"cut_plans{sfx}.json")
        if args.replan or not os.path.exists(plans_p):
            direct.run(args.project, max_cuts=args.max_cuts, brain=args.brain, tag=args.tag)
        else:
            print(f"[cut  ] using existing plans ({plans_p}); --replan to redo them")
        _plans = _json.load(open(plans_p))
        # Music beds depend ONLY on the plan, so queue them at FAL now: they
        # download while finalize encodes segments instead of each cut sitting
        # through the 10-13s queue+poll wait mid-chain. Same path finalize's
        # music step constructs (deliverables/audio/<id>_music.mp3), so its
        # cache check / wait_for joins these exact futures.
        _targets = products.delivery_targets(root, args.targets_for)
        if "master" not in _targets:
            _targets = _targets + ["master"]   # cut always reviews the master
        if any(products.platform_spec(t, profile)["mix"] == "music" for t in _targets):
            audir = os.path.join(root, "deliverables", "audio")
            os.makedirs(audir, exist_ok=True)
            for p in _plans:
                audio_post.prefetch_music(
                    p, os.path.join(audir, f"{p['id']}_music.mp3"),
                    os.path.basename(root))
            print(f"[cut  ] music beds prefetching for {len(_plans)} cut(s) "
                  "while segments render")
        # VFX (playbook G1-G6). Part of "what you review is what ships": a cut
        # judged without its graphics layer is judged on the wrong video.
        # Driven by edl/overlay_specs.json; silently skipped when a project has
        # none, because meme callouts are wrong for some footage on purpose.
        # Also skipped when the account's variant selection ships no _gfx file.
        spec = os.path.join(root, "edl", "overlay_specs.json")
        if not any(v.endswith("gfx") for v in wanted):
            print(f"[cut  ] variants ({', '.join(wanted)}) include no gfx "
                  f"file: skipping the graphics layer")
            args.skip_overlays = True
        gfx = None
        if not args.skip_overlays:
            if not os.path.exists(spec):
                # Plan the graphics layer from the frames themselves, so every
                # project gets content-aware marks without anyone hand-writing
                # coordinates. Logo comes from the per-machine config.
                print("[cut  ] planning graphics from frame content, per cut ...")
            elif args.replan:
                # A spec computed against the PREVIOUS plan generation is
                # stale data: stale card windows ran past the
                # new clips' ends and stretched four renders past their
                # duration gate. Replanned cuts get replanned graphics.
                print("[cut  ] --replan: overlay specs will be re-planned "
                      "against the new cut plans (stale windows caused the "
                      "duration-gate failures)")
            gfx = _CutGfx(args.project, args.product,
                          autoplan_needed=args.replan or not os.path.exists(spec),
                          tag=args.tag)
            print("[cut  ] graphics overlays (G1-G6) pipeline each cut as it lands ...")
        try:
            finalize.run(args.project, tag=args.tag, product=args.product,
                         targets_for=args.targets_for, review=True,
                         force=args.replan,
                         account=args.account, variants=args.variants,
                         on_cut=gfx.on_cut if gfx else None)
        except BaseException:
            if gfx:   # finalize died: drop queued gfx, don't hang on exit
                gfx.pool.shutdown(wait=False, cancel_futures=True)
            raise
        if gfx:
            gfx.finish()
            # gfx-only shipping: the base burn masters leave the ship dir for
            # deliverables/.cache once every gfx variant exists (otherwise
            # final/ held both cut_XX.mp4 and cut_XX_gfx.mp4).
            finalize.retire_unshipped_bases(root, wanted, _plans, sfx)
            # Re-point the review at the _gfx files the overlays just made.
            _t = products.delivery_targets(root, args.targets_for)
            print("Review updated: " + finalize._write_review_md(root, _plans, _t, sfx))
        elif not os.path.exists(spec):
            print("[cut  ] no overlay_specs.json: shipping without a graphics layer")
        # Semantic ending verification (designed endings): the vision model
        # confirms each outro-plan cut's payoff completes on screen before
        # the content ends; complete=false adjusts the plan's content end
        # and re-renders that cut ONCE. Runs before judge so the gate reads
        # the final verdicts. REELLY_ENDING_CHECK=off disables it.
        from . import ending_check
        if ending_check.enabled():
            ending_check.run(args.project, tag=args.tag, product=args.product,
                             account=args.account, variants=args.variants,
                             gfx=not args.skip_overlays)
        # Judge stays a final barrier: its per-file loop lives inside
        # judge.run and re-driving it per cut would mean duplicating that
        # logic out here. Overlays now overlap finalize; QC runs once, ordered.
        if not args.skip_judge:
            judge.run(args.project, tag=args.tag)
    elif args.cmd == "preview":
        from . import preview
        print("[note ] preview is a debug render: no music, SFX or end tag. "
              "Review from `reelly cut` instead, or you review twice.")
        preview.render(args.project, cut_id=args.cut, tag=args.tag)
    elif args.cmd == "burnin":
        from . import burnin
        burnin.run(args.video, project=args.project, out=args.out,
                   music=not args.no_music, cut_pauses=args.cut_pauses,
                   max_pause=args.max_pause)
    elif args.cmd == "longform":
        from . import longform
        longform.run(args.project, product=args.product, music=not args.no_music)
    elif args.cmd == "overlays":
        from . import overlays
        if args.auto:
            overlays.autoplan(args.project, kicker=args.kicker, label=args.label,
                              meme=not args.no_meme, src_dir=args.src, logo=args.logo,
                              cta=args.cta, product=args.product, cut_id=args.cut,
                              tag=args.tag)
        overlays.apply(args.project, src_dir=args.src, cut_id=args.cut, tag=args.tag)
    elif args.cmd == "sizzle":
        import os as _os
        from . import sizzle
        # Sizzle is landscape-first. Resolve the frame size: an explicit --size
        # wins; otherwise --social/--portrait selects the vertical feed variant,
        # and the default is landscape 1920x1080.
        if args.size:
            size = args.size
        elif args.social:
            size = "1080x1920"
        else:
            size = "1920x1080"
        args.size = size
        if args.survey_only:
            clips = sizzle._expand(args.clips)
            work = args.work or _os.path.join(
                _os.path.dirname(_os.path.abspath(args.out)), "_work")
            _os.makedirs(work, exist_ok=True)
            surveyed = sizzle.survey(clips, args.product,
                                     _os.path.join(work, "survey.json"),
                                     crop=sizzle.crop_map(args.crop),
                                     window=args.window,
                         no_cinecard=args.no_cinecard)
            keep, drops = sizzle.cleared(surveyed,
                                         allow_weapons=args.allow_weapons)
            print(f"\n{len(keep)} cleared, {len(drops)} dropped")
            for f, why in drops:
                print(f"  DROP {_os.path.basename(f)[:50]:50} {why}")
        elif args.script:
            sizzle.build_from_script(args.script, args.out, size=args.size,
                                     work=args.work,
                                     no_cinecard=args.no_cinecard)
        else:
            sizzle.build(args.product, args.clips, args.out,
                         seconds=args.seconds, size=args.size,
                         brain=args.brain, work=args.work, crop=args.crop,
                         allow_weapons=args.allow_weapons,
                         window=args.window, replan=args.replan)
    elif args.cmd == "reveal":
        from . import reveal
        reveal.build(args.cards, args.out, bg=args.bg, project=args.project or "")
    elif args.cmd == "motion":
        import json as _json
        from . import motion
        copy_override = _json.load(open(args.copy)) if args.copy else None
        message = args.message
        if not message and copy_override:
            fallback = copy_override.get("caption") or copy_override.get("hook")
            if isinstance(fallback, dict):
                fallback = fallback.get("text")
            message = str(fallback).strip() if fallback else None
        if not message:
            ap.error("motion requires --message unless --copy provides a caption or hook")
        motion.run(args.image, message, campaign=args.campaign, name=args.name,
                   tier="hero" if args.hero else "draft", brain=args.brain,
                   skip_judge=args.skip_judge, real_art=args.real_art,
                   copy_override=copy_override,
                   # getattr: tests and other callers build a Namespace by
                   # hand and will not carry newly added flags.
                   video_model=getattr(args, "video_model", "h3max"),
                   image_model=getattr(args, "image_model", "nano-banana"),
                   seconds=getattr(args, "seconds", None),
                   speech=getattr(args, "speech", False),
                   music=getattr(args, "music", True))
    elif args.cmd == "handoff":
        from . import handoff
        handoff.run(args.project, tag=args.tag)
    elif args.cmd == "finalize":
        from . import finalize
        finalize.run(args.project, cut_id=args.cut, tag=args.tag,
                     product=args.product, targets_for=args.targets_for,
                     force=args.force,
                     account=args.account, variants=args.variants)
    elif args.cmd == "newsletter":
        from . import newsletter
        newsletter.run(args.project, tag=args.tag, product=args.product)
    elif args.cmd == "deliver":
        from . import deliver
        deliver.run(args.project, tag=args.tag, product=args.product,
                    account=args.account, variants=args.variants,
                    targets_for=args.targets_for)
    elif args.cmd == "learn":
        from . import learn
        learn.run(project=args.project, metrics_path=args.metrics, tag=args.tag)
    elif args.cmd == "clear":
        from . import clearance, direct
        clearance.mark_cleared(direct.resolve_project(args.project),
                               args.speaker, cleared=args.cleared,
                               by=args.by, note=args.note)
    elif args.cmd == "judge":
        import sys

        from . import judge
        sys.exit(1 if judge.run(args.project, tag=args.tag,
                                visual=args.visual) else 0)
    elif args.cmd == "budget":
        if args.bcmd == "sprint":
            until = ledger.start_sprint(args.cap, args.days, args.reason)
            print(f"sprint cap ${args.cap:.2f} until {until}" +
                  (f" ({args.reason})" if args.reason else ""))
        elif args.bcmd == "endsprint":
            ledger.end_sprint()
            print("sprint ended, default cap restored")
        else:
            print(ledger.report())


if __name__ == "__main__":
    main()
