from PIL import Image
from pathlib import Path

root = Path(__file__).resolve().parents[1]
img_in = root / 'frontend' / 'img' / 'nhit-logo.png'
if not img_in.exists():
    raise SystemExit(f"Source image not found: {img_in}")

sizes = [(192, 'nhit-192.png'), (512, 'nhit-512.png')]
for size, name in sizes:
    out = root / 'frontend' / 'img' / name
    with Image.open(img_in) as im:
        im = im.convert('RGBA')
        im.thumbnail((size, size), Image.LANCZOS)
        # create square canvas and center
        canvas = Image.new('RGBA', (size, size), (255,255,255,0))
        x = (size - im.width) // 2
        y = (size - im.height) // 2
        canvas.paste(im, (x, y), im)
        canvas.save(out, format='PNG')
        print(f'Wrote {out}')
