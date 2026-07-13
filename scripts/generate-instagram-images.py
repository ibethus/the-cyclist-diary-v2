#!/usr/bin/env python3
"""
Génère une image « Instagram » (portrait 3:4) pour chaque article du blog
et chaque étape d'aventure, à partir de sa couverture ``index.jpg``.

Pour chaque *page bundle* (dossier contenant ``index.md`` / ``index.<lang>.md``)
situé sous ``content/blog`` ou ``content/adventures`` :

1. On récupère la couverture ``index.jpg`` / ``index.jpeg`` du dossier.
   Si aucune n'existe, on copie une image du dossier (choisie de façon
   déterministe) en ``index.jpg`` — l'auteur pourra la remplacer plus tard.
2. On recadre / redimensionne en portrait 3:4.
3. Pour une étape d'aventure (« sur le vif »), on écrit le nom de l'aventure
   en haut de l'image.
4. On écrit le titre de l'article en bas de l'image.
5. On exporte le résultat en JPEG sous le nom ``instagram.jpeg`` dans le
   dossier du bundle. Hugo le publie alors comme ressource de page, et le
   flux RSS l'utilise dans la balise ``<enclosure>``.

La police et les couleurs reprennent celles du blog (police de titre
LTCushion-Medium, vert #708A58 sur bandeau crème #FFF1CA) pour rester cohérent.

Ce script doit être lancé AVANT ``hugo`` (voir le workflow GitHub Actions),
afin que les fichiers ``instagram.jpeg`` soient pris en compte au build.
"""
from __future__ import annotations

import random
import re
import shutil
import sys
import tomllib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# --- Chemins ---------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
CONTENT_DIR = ROOT / "content"
FONT_PATH = ROOT / "assets" / "fonts" / "LTCushion-Medium.ttf"

# Sous-arbres traités : articles de blog et étapes d'aventure.
SECTIONS = ("blog", "adventures")

# --- Format de sortie ------------------------------------------------------
IG_WIDTH, IG_HEIGHT = 1080, 1440  # portrait 3:4
JPEG_QUALITY = 88
OUTPUT_NAME = "instagram.jpeg"

# --- Charte graphique du blog ---------------------------------------------
BRAND_GREEN = (112, 138, 88)          # --the-cyclist-diary-green
BRAND_CREAM = (255, 241, 202)         # --the-cyclist-diary-yellow
BAND_ALPHA = 235                      # opacité du bandeau crème (0-255)
ACCENT_THICKNESS = 6                  # épaisseur du liseré vert

# --- Recherche des couvertures / images -----------------------------------
COVER_STEMS = ("index.jpg", "index.jpeg")
PHOTO_EXTS = (".jpg", ".jpeg")        # candidats prioritaires pour la couverture
FALLBACK_EXTS = (".png",)             # utilisés seulement si aucun JPEG
DEFAULT_LANG = "fr"
# Langue des textes gravés sur l'image : le profil Instagram est en anglais,
# on privilégie donc les titres de index.en.md / _index.en.md.
TEXT_LANG = "en"

# Mise en page du texte
TEXT_MAX_WIDTH_RATIO = 0.30  # largeur max du texte (~ un seul mot par ligne)
LINE_SPACING = 14          # espace vertical entre les lignes
TITLE_MAX_LINES = 6        # titre réparti sur plusieurs lignes (presque 1 mot/ligne)
TITLE_START_SIZE = 64      # taille de police de départ (pas trop grande)
TITLE_MIN_SIZE = 34
SHADOW_OFFSET = 3          # décalage de l'ombre portée (lisibilité)
SHADOW_BLUR = 6            # flou de l'ombre portée
SHADOW_ALPHA = 200         # opacité de l'ombre (0-255)


def read_front_matter(md_path: Path) -> dict:
    """Extrait le front matter TOML (``+++``) d'un fichier Markdown."""
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    match = re.match(r"^\s*\+\+\+\s*\n(.*?)\n\+\+\+", text, re.DOTALL)
    if not match:
        return {}
    try:
        return tomllib.loads(match.group(1))
    except tomllib.TOMLDecodeError:
        # Repli : on récupère au moins le titre.
        title_match = re.search(r'title\s*=\s*["\'](.+?)["\']', match.group(1))
        return {"title": title_match.group(1)} if title_match else {}


def bundle_markdown(bundle: Path) -> Path | None:
    """Retourne le fichier index.md du bundle (langue par défaut en priorité)."""
    candidates = [
        bundle / f"index.{DEFAULT_LANG}.md",
        bundle / "index.md",
    ]
    candidates += sorted(bundle.glob("index.*.md"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def get_title(bundle: Path) -> str | None:
    """
    Titre de l'article gravé sur l'image.

    On lit en priorité le titre anglais (``index.en.md``) car le profil
    Instagram est en anglais, avec repli sur la langue par défaut.
    """
    for lang in (TEXT_LANG, DEFAULT_LANG):
        md = bundle / f"index.{lang}.md"
        if md.exists():
            title = read_front_matter(md).get("title")
            if title:
                return str(title).strip()
    # Repli ultime : index.md ou tout autre index.*.md.
    md = bundle_markdown(bundle)
    if md is not None:
        title = read_front_matter(md).get("title")
        return str(title).strip() if title else None
    return None


def get_adventure_name(bundle: Path) -> str | None:
    """
    Pour une étape d'aventure, retourne le nom de l'aventure parente.

    L'aventure est le plus proche ancêtre (sous ``content/adventures``)
    possédant un ``_index.<lang>.md``. Le nom anglais (``_index.en.md``) est
    privilégié pour rester cohérent avec le profil Instagram.
    """
    adventures_root = CONTENT_DIR / "adventures"
    if adventures_root not in bundle.parents:
        return None

    # On remonte du parent jusqu'à (et sans dépasser) content/adventures,
    # en cherchant le premier dossier qui est une aventure (_index.*.md).
    parent = bundle.parent
    while True:
        index_files = list(parent.glob("_index.*.md"))
        if index_files:
            localized = parent / f"_index.{TEXT_LANG}.md"
            if not localized.exists():
                localized = parent / f"_index.{DEFAULT_LANG}.md"
            fm = read_front_matter(localized if localized.exists() else index_files[0])
            name = fm.get("title")
            return str(name).strip() if name else parent.name
        if parent == adventures_root:
            return None
        parent = parent.parent


def is_adventure_stage(bundle: Path) -> bool:
    """Vrai si le bundle est une étape sous ``content/adventures``."""
    adventures_root = CONTENT_DIR / "adventures"
    return adventures_root in bundle.parents


def find_leaf_bundles() -> list[Path]:
    """Liste les *page bundles* (index.md) sous blog/ et adventures/."""
    bundles: list[Path] = []
    for section in SECTIONS:
        section_dir = CONTENT_DIR / section
        if not section_dir.exists():
            continue
        for md in section_dir.rglob("index*.md"):
            # On ne veut que les vrais leaf bundles (index.md / index.<lang>.md),
            # pas les branch bundles (_index.md).
            if md.name.startswith("_index"):
                continue
            if not re.match(r"index(\.[a-z]{2})?\.md$", md.name):
                continue
            bundle = md.parent
            if bundle not in bundles:
                bundles.append(bundle)
    return bundles


def ensure_cover(bundle: Path) -> Path | None:
    """
    Retourne la couverture ``index.jpg`` du bundle.

    Si aucune couverture n'existe, copie une image du dossier (choisie de
    façon déterministe, à partir du chemin du bundle) en ``index.jpg``.
    """
    for stem in COVER_STEMS:
        cover = bundle / stem
        if cover.exists():
            return cover

    # Aucune couverture : on choisit une image à la racine du bundle.
    photos = sorted(
        p for p in bundle.iterdir()
        if p.is_file() and p.suffix.lower() in PHOTO_EXTS and p.name.lower() not in COVER_STEMS
    )
    if not photos:
        photos = sorted(
            p for p in bundle.iterdir()
            if p.is_file() and p.suffix.lower() in FALLBACK_EXTS
        )
    if not photos:
        return None

    # Choix déterministe (reproductible d'un build à l'autre).
    rng = random.Random(str(bundle.relative_to(CONTENT_DIR)))
    chosen = rng.choice(photos)
    cover = bundle / "index.jpg"
    if chosen.suffix.lower() in PHOTO_EXTS:
        shutil.copyfile(chosen, cover)
    else:
        # PNG (ou autre) : on convertit en JPEG.
        with Image.open(chosen) as img:
            img.convert("RGB").save(cover, "JPEG", quality=JPEG_QUALITY)
    print(f"   🖼️  Couverture manquante -> copie de « {chosen.name} » en index.jpg")
    return cover


def crop_to_portrait(img: Image.Image) -> Image.Image:
    """Recadre au centre en 3:4 puis redimensionne en 1080x1440."""
    img = img.convert("RGB")
    target_ratio = IG_WIDTH / IG_HEIGHT
    w, h = img.size
    ratio = w / h
    if ratio > target_ratio:
        # Trop large : on rogne les côtés.
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:
        # Trop haut : on rogne en haut et en bas.
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    return img.resize((IG_WIDTH, IG_HEIGHT), Image.LANCZOS)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Découpe le texte en lignes tenant dans ``max_width``."""
    lines: list[str] = []
    for paragraph in text.splitlines() or [text]:
        words = paragraph.split()
        if not words:
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if draw.textlength(candidate, font=font) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_lines: int,
    start_size: int,
    min_size: int,
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Trouve la plus grande taille de police telle que le texte tienne."""
    best: tuple[ImageFont.FreeTypeFont, list[str]] | None = None
    for size in range(start_size, min_size - 1, -2):
        font = ImageFont.truetype(str(FONT_PATH), size)
        lines = wrap_text(draw, text, font, max_width)
        fits_width = all(draw.textlength(line, font=font) <= max_width for line in lines)
        if len(lines) <= max_lines and fits_width:
            return font, lines
        best = (font, lines)
    # Aucune taille idéale : on garde la plus petite.
    font = ImageFont.truetype(str(FONT_PATH), min_size)
    return font, wrap_text(draw, text, font, max_width)


def draw_centered_title(base: Image.Image, text: str) -> None:
    """
    Grave le titre au centre exact de l'image : texte beige, réparti sur
    plusieurs lignes (presque un mot par ligne grâce à une largeur réduite),
    centré horizontalement et verticalement, sans bandeau de couleur. Une
    ombre portée douce garantit la lisibilité sur les fonds clairs.
    """
    draw = ImageDraw.Draw(base)
    # Style minuscule : on retire toutes les majuscules du titre affiché.
    text = text.lower()
    # Un séparateur " - " (espace tiret espace) est plus joli avec le tiret
    # isolé sur sa propre ligne : on force les sauts de ligne autour.
    text = text.replace(" - ", "\n-\n")
    max_width = int(IG_WIDTH * TEXT_MAX_WIDTH_RATIO)
    font, lines = fit_text(
        draw, text, max_width, TITLE_MAX_LINES, TITLE_START_SIZE, TITLE_MIN_SIZE
    )

    ascent, descent = font.getmetrics()
    line_height = ascent + descent
    block_height = len(lines) * line_height + (len(lines) - 1) * LINE_SPACING

    # Centrage vertical dans l'image.
    top = (IG_HEIGHT - block_height) // 2

    # Calque d'ombre portée (texte noir décalé + flou).
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    y = top
    for line in lines:
        line_width = draw.textlength(line, font=font)
        x = (IG_WIDTH - line_width) / 2
        shadow_draw.text(
            (x + SHADOW_OFFSET, y + SHADOW_OFFSET),
            line,
            font=font,
            fill=(0, 0, 0, SHADOW_ALPHA),
        )
        y += line_height + LINE_SPACING
    shadow = shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))
    base.alpha_composite(shadow)

    # Texte beige, centré.
    draw = ImageDraw.Draw(base)
    y = top
    for line in lines:
        line_width = draw.textlength(line, font=font)
        x = (IG_WIDTH - line_width) / 2
        draw.text((x, y), line, font=font, fill=(*BRAND_CREAM, 255))
        y += line_height + LINE_SPACING



def generate_for_bundle(bundle: Path) -> bool:
    """Génère ``instagram.jpeg`` pour un bundle. Retourne True si succès."""
    title = get_title(bundle)
    if not title:
        print(f"   ⚠️  Titre introuvable, bundle ignoré : {bundle}")
        return "failed"

    cover = ensure_cover(bundle)
    if cover is None:
        print(f"   ⚠️  Aucune image disponible, bundle ignoré : {bundle}")
        return "failed"

    try:
        with Image.open(cover) as src:
            canvas = crop_to_portrait(src).convert("RGBA")
    except OSError as exc:
        print(f"   ⚠️  Image illisible ({cover.name}) : {exc}")
        return "failed"

    draw_centered_title(canvas, title)

    out_path = bundle / OUTPUT_NAME
    canvas.convert("RGB").save(out_path, "JPEG", quality=JPEG_QUALITY, optimize=True)
    rel = bundle.relative_to(CONTENT_DIR)
    print(f"   ✅ {rel}/{OUTPUT_NAME}  «{title}»")
    return True


def main() -> None:
    print("🎨 Génération des images Instagram (portrait 3:4)")
    print("=" * 60)

    if not FONT_PATH.exists():
        print(f"❌ Police introuvable : {FONT_PATH}")
        sys.exit(1)
    if not CONTENT_DIR.exists():
        print(f"❌ Dossier content introuvable : {CONTENT_DIR}")
        sys.exit(1)

    bundles = find_leaf_bundles()
    print(f"📦 {len(bundles)} article(s) / étape(s) détecté(s)\n")

    generated = 0
    for bundle in bundles:
        if generate_for_bundle(bundle):
            generated += 1

    print("\n" + "=" * 60)
    print(f"✅ {generated}/{len(bundles)} image(s) « {OUTPUT_NAME} » générée(s)")


if __name__ == "__main__":
    main()
