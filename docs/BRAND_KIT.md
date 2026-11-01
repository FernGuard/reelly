# Brand kit

A brand kit is how Reelly looks, sounds and writes for you. It lives on your
machine, outside the repository: `~/.reelly/brandkit/brands/<name>/`, with a
`brand.json` and the font and sound files it names. One kit can hold several
brands, for example a studio and each of its games.

```sh
reelly brand add mybrand --headline Display-Bold.ttf --caption Text-Bold.ttf \
    --accent "#7c5cff" --ink "#f4f1ea" --surface "#0e0e12"
reelly brand check mybrand          # validate before you render
reelly cut <project> --brand mybrand
```

Pick a brand per run with `--brand NAME` (anywhere on the command line) or
`REELLY_BRAND`, or set `"default_brand"` in `~/.reelly/brandkit/kit.json`.
Every command that renders validates the active brand first and stops at once
on an error, so a typo never switches an effect off quietly.

`examples/brandkit/demo/` is a complete starting point.

## brand.json (schema 1)

```json
{
  "schema": 1,
  "name": "mybrand",
  "accent": "#7c5cff",
  "ink": "#f4f1ea",
  "surface": "#0e0e12",
  "fonts": {"headline": "Display-Bold.ttf", "caption": "Text-Bold.ttf"},

  "copy": {
    "audience": "who this is for, one line",
    "promise": "what they get, one line",
    "proof": "why it is true, one line",
    "voice": "how it sounds: warm, dry, playful...",
    "cta": "Follow for more",
    "cta_hot": "Follow",
    "avoid": ["words and claims never to write"]
  },

  "video": {"fps": 60, "reframe": "full_width", "content_type": "gameplay", "clip_len": [25, 30]},
  "text": {"motion": "blocklock", "exit": "petals", "petal_color": "#ffa322", "behind_subject": false},
  "effects": {
    "endcard": "smoke",
    "ambient": ["smoke", {"preset": "embers", "params": {"u_density": 0.4}}, "focus", "impact"],
    "text": "smoke",
    "finish": null
  },

  "music": {
    "tracks": {"calm": "Calm_80bpm.wav", "battle": "Battle_125bpm.wav"},
    "match": {"battle": ["fight", "combo", "boss"]},
    "default": "calm",
    "cleared": true
  },
  "sfx": {"impact": "Hit.ogg"}
}
```

### Identity

| Key | What it does |
|---|---|
| `accent` | The one brand colour: hot words, karaoke highlight, chips, effect tint. |
| `ink` | Text colour. |
| `surface` | Plates and backdrops. |
| `fonts` | Font files in the brand folder per role. `headline` sets hooks and the end card, `caption` sets captions and labels (a missing headline borrows caption). Static fonts only: cut variable fonts to fixed instances first (fontTools `varLib.instancer`). |

### copy

The brief every writing step reads: the editor brain, the hook writer and the
judge. `cta` is used verbatim on the end card and `cta_hot` is the word that
pops. `reelly brand verdict NAME --approve "line" --reject "line"` records
`approved_lines` and `rejected_lines`, which teach the writer the brand's
taste.

### video

| Key | Values | Default |
|---|---|---|
| `fps` | 24, 25, 30, 50, 60 | 30 |
| `reframe` | `"full_width"` (the whole 16:9 picture, nothing cropped) or a zoom from 1.0 to 2.0 (crops the sides) | Reelly chooses |
| `content_type` | `gameplay`, `talking-head`, `podcast`, `screen-demo` | a guess from the framing |
| `clip_len` | `[min, max]` seconds, 5 to 180 | `[20, 28]` |

The content type decides which effects suit the footage (no impact marks on a
podcast) and whether faces are protected. See [EFFECTS.md](EFFECTS.md).

### text

| Key | Values | Default |
|---|---|---|
| `motion` | `rise` (words rise and unblur in) or `blocklock` (words drop in, bounce and lock) | `rise` |
| `exit` | `fade` or `petals` (letters burn away into drifting petals) | `fade` |
| `petal_color` | second petal colour, `#RRGGBB` (the first is the accent) | `#ffa322` |
| `behind_subject` | `true`: hook text passes behind a person (talking-head and podcast only; experimental) | `false` |

### effects

One preset name per placement (`ambient` takes a list, bottom layer first), or
`{"preset": name, "params": {...}}` to tune a preset. `null` or `"off"` turns a
placement off.

| Placement | Presets | Where it plays |
|---|---|---|
| `endcard` | `smoke` | the end card after the content |
| `ambient` | `smoke`, `embers`, `impact`, `focus` | the space around the picture, the whole cut |
| `text` | `smoke` | around kinetic text |
| `finish` | `folk-print` (experimental) | a print finish over the space around the picture |

Every preset and parameter is described in [EFFECTS.md](EFFECTS.md).

### music and sfx

Sound files sit in the brand folder under `music/` and `sfx/`. A cut takes the
track the plan names, else the first `match` whose words appear in what the
cut is about, else `default`. A tempo in the file name (`Battle_125bpm.wav`),
or `"bpm": {"battle": 125}`, puts hot-word pops exactly on the beat.
`"cleared": true` records that you hold the rights to use the music in
marketing; until then every run reminds you to check.

## Upgrading an older kit

Kits from before schema 1 used `fx`, `outro_fx`, `fps`, `reframe` and
`copy.clip_len`. `reelly brand check` names each moved key, and

```sh
reelly brand migrate mybrand            # print the result
reelly brand migrate mybrand --write    # save it (keeps brand.json.bak)
```

rewrites the file.
