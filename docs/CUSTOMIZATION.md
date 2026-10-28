# Private customization

The public repository contains neutral examples only. Put organization-specific settings under `~/.reelly` so they cannot be committed accidentally.

## Accounts

Create `~/.reelly/accounts.json`:

```json
{
  "my-native-account": {
    "posts_natively": true,
    "trending_audio": true,
    "variants": ["plain", "gfx", "trending", "trending_gfx"],
    "note": "internal note"
  },
  "my-managed-account": {
    "posts_natively": false,
    "trending_audio": false,
    "variants": ["gfx"]
  }
}
```

Select a profile with `--account my-native-account` or in a project's `delivery.json`:

```json
{"account": "my-native-account", "variants": ["gfx"], "targets": ["tiktok", "reels", "shorts"]}
```

## Products

Create `~/.reelly/products.json`:

```json
{
  "video": {
    "name": "Your Product",
    "url": "https://your-domain.example/product",
    "campaign": "video-campaign",
    "end_tag": "Made with Your Product"
  }
}
```

The built-in keys are `video`, `story`, `games`, and `adventure`. Override them locally rather than committing real brands or campaign identifiers.

## Logos and project location

Create `~/.reelly/config.json` and keep it at mode `0600`:

```json
{
  "projects": "~/reelly-projects",
  "logos": {
    "video": "/absolute/path/to/video-logo.png"
  }
}
```

You can also set `REELLY_PROJECTS` and `REELLY_BRANDKIT` in the environment.

## Brand kit

```sh
uv run python -m reelly.brandkit
```

This creates local files under `~/.reelly/brandkit/`, including `copy_bank.yaml`. Put private copy rules, banned names, fonts, music manifests, and generated end cards there. Do not move those files into the repository.

### Brands

One kit can hold several brands, for example a studio and each of its games:

```text
~/.reelly/brandkit/brands/<brand>/
  brand.json        {"name", "accent", "ink", "surface", "fonts": {"headline": "...", "caption": "..."}}
  <font files>      .ttf / .otf named in brand.json
```

Register one with a single command (files are copied into your local kit;
nothing goes into the repository):

```sh
reelly brand add mygame --headline Display-Bold.ttf --caption Text-Bold.ttf \
    --accent "#3366ff" --ink "#f4f1ea" --surface "#101010"
reelly brand list
```

Pick one per run with `--brand <brand>` (anywhere on the command line) or
`REELLY_BRAND`, or set `"default_brand"` in `kit.json`. The hook and end card
use the `headline` font, captions and labels use `caption` (a missing headline
borrows caption). The accent drives karaoke highlights, chips and the outro
tint; `ink` is the text colour. Overlay and card pages load the brand's own
font files locally instead of a font CDN. Pillow needs static fonts: cut
variable fonts to fixed instances first (for example with fontTools'
`varLib.instancer`).

## Verdicts and analytics

`playbook/feedback/VERDICTS.md` is a public empty template. Store real human judgments and performance exports in a private project or data store. Use synthetic examples in tests and documentation.
