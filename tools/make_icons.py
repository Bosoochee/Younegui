"""Génère assets/icon.png et assets/presplash.png (outil de dev : pip install pillow).

Style "bouton lecture" : rectangle arrondi vert et triangle blanc, sur fond blanc arrondi.
"""
import os

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = 1024  # dessin en 1024 px puis réduction, pour des bords lisses
GREEN_TOP = (46, 204, 113)
GREEN_BOTTOM = (22, 160, 80)


def gradient(size, top, bottom):
    img = Image.new("RGB", size)
    draw = ImageDraw.Draw(img)
    for y in range(size[1]):
        t = y / (size[1] - 1)
        draw.line([(0, y), (size[0], y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    return img


def play_button(with_background):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if with_background:
        draw.rounded_rectangle([24, 24, S - 24, S - 24], radius=220, fill=(255, 255, 255, 255))

    # Rectangle arrondi vert (proportions 10:7), avec une légère ombre
    w, h = 760, 532
    box = [(S - w) // 2, (S - h) // 2, (S + w) // 2, (S + h) // 2]
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([box[0], box[1] + 18, box[2], box[3] + 18], radius=150,
                                             fill=(0, 80, 30, 90))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(22)))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=150, fill=255)
    img.paste(gradient((S, S), GREEN_TOP, GREEN_BOTTOM).convert("RGBA"), (0, 0), mask)

    # Triangle blanc "lecture", aux coins adoucis
    cx, cy = S / 2 + 22, S / 2
    tri = [(cx - 105, cy - 140), (cx - 105, cy + 140), (cx + 150, cy)]
    tri_img = Image.new("L", (S, S), 0)
    ImageDraw.Draw(tri_img).polygon(tri, fill=255)
    tri_img = tri_img.filter(ImageFilter.GaussianBlur(6)).point(lambda v: 255 if v > 128 else 0)
    img.paste(Image.new("RGBA", (S, S), (255, 255, 255, 255)), (0, 0), tri_img)
    return img


def main():
    assets = os.path.join(ROOT, "assets")
    os.makedirs(assets, exist_ok=True)
    play_button(True).resize((512, 512), Image.LANCZOS).save(os.path.join(assets, "icon.png"))
    # Écran de démarrage (fond sombre de l'appli) : uniquement le bouton vert
    splash = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    splash.alpha_composite(play_button(False).resize((320, 320), Image.LANCZOS), (96, 96))
    splash.save(os.path.join(assets, "presplash.png"))
    print("ok")


if __name__ == "__main__":
    main()
