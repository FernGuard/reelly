# Example brand kit

`demo/brand.json` turns on the look a fresh install does not: block-lock
kinetic type with a petal exit, a smoky aura on the text, smoke and embers in
the space around the picture, and the smoke end card. Copy it into your kit
and run with it:

```sh
mkdir -p ~/.reelly/brandkit/brands
cp -r examples/brandkit/demo ~/.reelly/brandkit/brands/
reelly brand check demo
reelly cut <project> --brand demo
```

It uses your system fonts. Add your own type with
`reelly brand add demo --headline Display.ttf --caption Text.ttf` (the font
files are copied into your kit, never into the repository). Every key is
described in [docs/BRAND_KIT.md](../../docs/BRAND_KIT.md).
