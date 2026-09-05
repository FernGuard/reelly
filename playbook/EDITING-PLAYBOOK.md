# Editing Playbook

Public defaults for Reelly. These rules are craft-based starting points, not private performance data. Put organization-specific rules and evidence in files outside the repository.

## Workflow

- **W1:** Review the finished video, not a rough render. `reelly cut` is the normal review path; `reelly preview` is for debugging.
- Keep generated media, captions, sound, and graphics visible in the artifact being reviewed.

## Hooks

- **H1:** Something should move or change in the opening half-second.
- **H2:** Put the text hook on screen immediately; keep it concise and readable.
- **H3:** Do not open mid-word, on a filler word, or during an inhale.
- **H4:** For process content, consider showing the result before the process.
- **H5:** Treat the first frame and first line as one design decision.
- **H6:** Hold the hook long enough to read.

## Cuts and pacing

- **C1:** Cut on pauses, never through a word.
- **C2:** Avoid long stretches without a meaningful visual change.
- **C3:** Remove filler and false starts in short-form work; leave natural breathing room in long-form work.
- **C4:** Every clip needs a complete thought: setup, moment, landing.
- **C5:** End on a resolved beat, loopable frame, or useful question.
- **C6:** Show the payoff before the clip ends.
- **C7:** A problem-focused clip should include the solution or result.
- **C8:** Prefer clear silence for cut points and add small safety padding for timestamp drift.
- **C9:** Let laughs, gasps, and other reactions finish.
- **C10:** Leave a little air at speaker handoffs.
- **C11:** When a recording contains retakes, prefer the final complete take.
- **C12:** Collapse excessive pauses without removing all breathing room.
- **C13:** If a time skip needs emphasis, use restrained, consistent motion.

## Captions

- **CA1:** Keep cues short, readable, and aligned with phrase boundaries.
- **CA2:** Short-form can use burned captions; long-form should also ship an SRT sidecar.
- **CA3:** Keep captions clear of platform interface zones.
- **CA4:** Use concise, natural language.
- **CA5:** Correct product and domain vocabulary before rendering.
- **CA6:** Generate captions from the final picture-locked edit.
- **CA-sanitize:** Drop invalid or zero-duration ASR words before grouping cues.

## Sound

- **S1:** Normalize speech consistently and protect true peak.
- **S2:** Duck music beneath speech.
- **S3:** Use sound effects sparingly and intentionally.
- **S4:** Record the provider/model provenance of generated audio.
- **S5:** Let the dominant element, speech or music, determine edit rhythm.
- **S6:** Apply a light voice cleanup chain to final deliverables.
- **S7:** Use short fades at segment boundaries to prevent clicks.
- **S8:** Avoid stream-copy concatenation of independently AAC-encoded segments.
- **S9:** Measure the delivered file after encoding, not only the source mix.

## Composition and graphics

- **CO1:** Use layouts that keep both the speaker and relevant screen content legible.
- **CO2:** Place captions where they do not hide the subject.
- **CO3:** Protect platform-safe margins.
- **CO4:** Synchronize multi-source audio by measurement.
- **CO5:** Keep faces naturally framed with headroom.
- **CO6:** Use a picture-in-picture fallback when the screen needs more space.
- **CO7:** Reframe toward active content rather than the full desktop.
- **CO8:** The rendered review and editor handoff should match.

- **G1:** Use a consistent visual language.
- **G2:** Treat placement as a zone system, not a fixed coordinate.
- **G3:** A callout must point at the thing it names.
- **G4:** Overlay labels must describe what is actually shown.
- **G5:** Limit simultaneous attention-demanding elements.
- **G6:** Composite graphics onto the same final render that is reviewed.
- **G7:** Prefer calm, unoccupied regions for text.
- **G8:** Keep graphics inside the actual content band.
- **G9:** Choose size and contrast from the frame.
- **G10:** Use one closing message.
- **G11:** Keep the call to action inside a safe lower-third region.
- **G12:** Hold the final card long enough to read without covering the payoff.

## Generated media

- **M1:** The clip must deliver what its hook promises.
- **M2:** Review a lower-cost draft before requesting an expensive hero render.
- **M3:** Place graphics from the generated frames, not guessed coordinates.
- **M4:** Record model, prompt, source, and tier for generated media.
- **M5:** Use one call to action.
- **M6:** Remove baked text from generation references.
- **M7:** Let the compositor render type; do not ask the video model to spell it.
- **M8:** Keep copy short, specific, and natural.
- **M9:** Do not write in the voice of a person or organization without authorization.
- **M10:** When animating real artwork or product UI, do not invent or alter product details.

## Design checks

- **D1:** Treat the subject as an occupied zone.
- **D2:** Use one primary brand moment per frame.
- **D3:** Anchor text to a readable region or treatment.
- **D4:** Present one primary element at a time.
- **D5:** Judge the composed frame, not isolated layers.
- **D6:** Keep comfortable margins around subjects and platform chrome.
- **D7:** Choose text colors for contrast rather than palette similarity alone.

## Platform notes

- Vertical feeds generally need 9:16 output and protected interface margins.
- Store/trailer assets generally need 16:9 output and a resolved ending.
- Mark AI-generated media when the destination platform requires it.
- Confirm that you have rights and consent for all source media.
