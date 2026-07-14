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

import argparse
import json
import random
import re
import shutil
import sys
import tomllib
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

# --- Chemins ---------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
CONTENT_DIR = ROOT / "content"
FONT_PATH = ROOT / "assets" / "fonts" / "LTCushion-Medium.ttf"

# Sous-arbres traités : articles de blog et étapes d'aventure.
SECTIONS = ("blog", "adventures")

# --- Format de sortie ------------------------------------------------------
IG_WIDTH, IG_HEIGHT = 1080, 1440  # portrait 3:4
JPEG_QUALITY = 100
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

# --- Étalonnage « pellicule » : grain photo + colorimétrie pastel ----------
# Toutes ces valeurs sont paramétrables pour ajuster le rendu au goût.
FILM_LOOK_ENABLED = True
# Saturation : 1.0 = couleurs d'origine, <1 = plus délavé / pastel.
FILM_SATURATION = 0.75
# Contraste : <1 = plus doux, aspect mat façon pellicule.
FILM_CONTRAST = 0.85
# Luminosité : léger relèvement pour un rendu doux et lumineux.
FILM_BRIGHTNESS = 1.03
# Fade / matte : on relève les noirs pour un rendu délavé.
# 0 = noirs purs, ~15-40 = aspect pastel prononcé.
FILM_BLACK_LIFT = 20
# Teinte injectée dans les ombres relevées (crème chaude par défaut).
FILM_FADE_TINT = (255, 241, 202)
# Balance colorimétrique par canal (multiplicateurs R, V, B).
# >1 sur le rouge et <1 sur le bleu = rendu chaud / vintage.
FILM_COLOR_BALANCE = (1.04, 1.0, 0.96)
# Grain photo : intensité du bruit gaussien (0 = aucun, ~8-20 = visible).
FILM_GRAIN_INTENSITY = 2
# Grain monochrome (True) ou coloré (False, plus « numérique »).
FILM_GRAIN_MONOCHROME = True
# Vignetage : assombrissement progressif des bords (aspect argentique).
FILM_VIGNETTE_ENABLED = True
# Force du vignetage : 0 = aucun, 1 = bords quasi noirs. ~0.3-0.5 = subtil.
FILM_VIGNETTE_STRENGTH = 0.5
# Rayon (0-1) où le vignetage commence : plus grand = zone claire centrale
# plus large, assombrissement concentré sur les coins.
FILM_VIGNETTE_RADIUS = 0.60

# --- Statistiques d'étape (distance / dénivelé / durée) --------------------
# Lues depuis le fichier « *.polyline.json » présent dans le bundle et
# affichées en bas de l'image, réparties uniformément sur la largeur.
STATS_ENABLED = True
# Champs affichés, dans l'ordre (gauche -> droite). Valeurs possibles :
# "distance", "elevation", "duration".
STATS_FIELDS = ("distance", "elevation", "duration")
# Affiche un petit libellé sous chaque valeur.
STATS_SHOW_LABELS = False
# Libellés (repris tels quels, en minuscules pour rester cohérent).
STATS_LABELS = {
    "distance": "distance",
    "elevation": "dénivelé",
    "duration": "durée",
}
STATS_VALUE_SIZE = 36       # taille de police des valeurs
STATS_LABEL_SIZE = 22       # taille de police des libellés
STATS_LABEL_GAP = 8         # espace vertical valeur <-> libellé
STATS_BOTTOM_MARGIN = 50    # marge depuis le bas de l'image
STATS_COLOR = BRAND_CREAM   # couleur du texte des stats


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


def apply_vignette(img: Image.Image) -> Image.Image:
    """
    Assombrit progressivement les bords de l'image (vignetage argentique).

    Un masque radial est calculé : luminosité pleine jusqu'à
    ``FILM_VIGNETTE_RADIUS`` (fraction de la demi-diagonale), puis décroissance
    douce vers les coins, l'assombrissement maximal valant
    ``FILM_VIGNETTE_STRENGTH``.
    """
    w, h = img.size
    cx, cy = w / 2, h / 2
    max_dist = (cx ** 2 + cy ** 2) ** 0.5
    inner = FILM_VIGNETTE_RADIUS * max_dist

    # Rampe 1D (par distance) réutilisée via un gradient radial.
    mask = Image.new("L", (w, h), 0)
    # On construit le masque avec une ellipse floutée : centre clair (255),
    # bords sombres (0), puis on l'utilise pour interpoler vers une version
    # assombrie de l'image.
    radial = Image.radial_gradient("L").resize((w, h))
    # radial : 0 au centre -> 255 aux coins. On veut l'inverse pondéré.
    def _ramp(v: int) -> int:
        # v = 0 (centre) .. 255 (coin). Distance normalisée.
        dist = v / 255 * max_dist
        if dist <= inner:
            factor = 0.0
        else:
            factor = (dist - inner) / (max_dist - inner)
            factor = min(1.0, factor)
        # 255 = pleine luminosité, plus bas = plus sombre.
        return int(255 * (1 - FILM_VIGNETTE_STRENGTH * factor))

    mask = radial.point(_ramp)
    dark = ImageEnhance.Brightness(img).enhance(1 - FILM_VIGNETTE_STRENGTH)
    return Image.composite(img, dark, mask)


def apply_film_look(img: Image.Image) -> Image.Image:
    """
    Applique un rendu « pellicule » : colorimétrie pastel (désaturation,
    contraste doux, teinte chaude, noirs relevés/matte) puis grain photo.

    Le traitement s'applique sur la photo AVANT la gravure du titre, afin que
    le texte reste net et propre par-dessus le grain. Tout est paramétrable
    via les constantes ``FILM_*``.
    """
    if not FILM_LOOK_ENABLED:
        return img.convert("RGB")

    img = img.convert("RGB")

    # 1. Désaturation légère -> aspect pastel / délavé.
    img = ImageEnhance.Color(img).enhance(FILM_SATURATION)
    # 2. Contraste plus doux -> rendu mat façon film.
    img = ImageEnhance.Contrast(img).enhance(FILM_CONTRAST)
    # 3. Léger relèvement de luminosité.
    img = ImageEnhance.Brightness(img).enhance(FILM_BRIGHTNESS)

    # 4. Balance colorimétrique par canal (teinte chaude / vintage).
    br, bg, bb = FILM_COLOR_BALANCE
    r, g, b = img.split()
    r = r.point(lambda v, m=br: min(255, int(v * m)))
    g = g.point(lambda v, m=bg: min(255, int(v * m)))
    b = b.point(lambda v, m=bb: min(255, int(v * m)))
    img = Image.merge("RGB", (r, g, b))

    # 5. Fade / matte : on remappe [0,255] -> [lift, 255] par canal, avec une
    #    teinte crème dans les noirs pour l'aspect pastel.
    if FILM_BLACK_LIFT > 0:
        tr, tg, tb = FILM_FADE_TINT
        lifts = (
            FILM_BLACK_LIFT * tr / 255,
            FILM_BLACK_LIFT * tg / 255,
            FILM_BLACK_LIFT * tb / 255,
        )
        channels = img.split()
        remapped = [
            ch.point(lambda v, lo=lift: int(lo + v * (255 - lo) / 255))
            for ch, lift in zip(channels, lifts)
        ]
        img = Image.merge("RGB", remapped)

    # 6. Vignetage : assombrissement progressif des bords.
    if FILM_VIGNETTE_ENABLED and FILM_VIGNETTE_STRENGTH > 0:
        img = apply_vignette(img)

    # 7. Grain photo : bruit gaussien additionné (centré sur 128).
    if FILM_GRAIN_INTENSITY > 0:
        if FILM_GRAIN_MONOCHROME:
            noise = Image.effect_noise(img.size, FILM_GRAIN_INTENSITY)
            noise = Image.merge("RGB", (noise, noise, noise))
        else:
            noise = Image.merge(
                "RGB",
                tuple(
                    Image.effect_noise(img.size, FILM_GRAIN_INTENSITY)
                    for _ in range(3)
                ),
            )
        # out = img + (noise - 128) -> grain signé ±intensité.
        img = ImageChops.add(img, noise, scale=1.0, offset=-128)

    return img


def wrap_text(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int
) -> list[str]:
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



def read_stats(bundle: Path) -> dict | None:
    """
    Lit les statistiques d'étape depuis le fichier ``*.polyline.json`` du
    bundle (distance, dénivelé, durée). Retourne ``None`` si absent/illisible.
    """
    candidates = sorted(bundle.glob("*.polyline.json"))
    if not candidates:
        return None
    try:
        data = json.loads(candidates[0].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    meta = data.get("metadata")
    return meta if isinstance(meta, dict) else None


def fmt_distance(km: float) -> str:
    """Ex. 25.657 -> « 25.7 km », 455.107 -> « 455 km »."""
    return f"{km:.0f} km" if km >= 100 else f"{km:.1f} km"


def fmt_elevation(gain_m: float) -> str:
    """Dénivelé positif, ex. 652.2 -> « +652 m »."""
    return f"+{round(gain_m)} m"


def fmt_duration(seconds: float) -> str:
    """Ex. 41651 -> « 11h34 », 2100 -> « 35 min »."""
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours}h{minutes:02d}" if hours else f"{minutes} min"


def stat_value(field: str, meta: dict) -> str | None:
    """Retourne la valeur formatée d'un champ de stats, ou ``None``."""
    if field == "distance" and meta.get("distanceKm") is not None:
        return fmt_distance(meta["distanceKm"])
    if field == "elevation" and meta.get("elevationGainM") is not None:
        return fmt_elevation(meta["elevationGainM"])
    if field == "duration" and meta.get("durationSeconds") is not None:
        return fmt_duration(meta["durationSeconds"])
    return None


def draw_stats(base: Image.Image, meta: dict) -> None:
    """
    Grave les statistiques en bas de l'image, réparties uniformément sur la
    largeur (une colonne centrée par champ), valeur au-dessus d'un libellé
    optionnel. Une ombre portée douce garantit la lisibilité.
    """
    columns: list[tuple[str, str | None]] = []
    for field in STATS_FIELDS:
        value = stat_value(field, meta)
        if value is not None:
            label = STATS_LABELS.get(field) if STATS_SHOW_LABELS else None
            columns.append((value, label))
    if not columns:
        return

    value_font = ImageFont.truetype(str(FONT_PATH), STATS_VALUE_SIZE)
    label_font = ImageFont.truetype(str(FONT_PATH), STATS_LABEL_SIZE)

    v_ascent, v_descent = value_font.getmetrics()
    value_h = v_ascent + v_descent
    label_h = sum(label_font.getmetrics()) if STATS_SHOW_LABELS else 0
    block_h = value_h + (STATS_LABEL_GAP + label_h if STATS_SHOW_LABELS else 0)
    top = IG_HEIGHT - STATS_BOTTOM_MARGIN - block_h

    col_width = IG_WIDTH / len(columns)

    # Calque d'ombre portée.
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    draw = ImageDraw.Draw(base)

    for i, (value, label) in enumerate(columns):
        center_x = col_width * (i + 0.5)
        # Valeur.
        vw = draw.textlength(value, font=value_font)
        vx = center_x - vw / 2
        shadow_draw.text(
            (vx + SHADOW_OFFSET, top + SHADOW_OFFSET),
            value, font=value_font, fill=(0, 0, 0, SHADOW_ALPHA),
        )
        # Libellé.
        if label:
            lw = draw.textlength(label, font=label_font)
            lx = center_x - lw / 2
            ly = top + value_h + STATS_LABEL_GAP
            shadow_draw.text(
                (lx + SHADOW_OFFSET, ly + SHADOW_OFFSET),
                label, font=label_font, fill=(0, 0, 0, SHADOW_ALPHA),
            )

    shadow = shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))
    base.alpha_composite(shadow)

    draw = ImageDraw.Draw(base)
    for i, (value, label) in enumerate(columns):
        center_x = col_width * (i + 0.5)
        vw = draw.textlength(value, font=value_font)
        draw.text(
            (center_x - vw / 2, top),
            value, font=value_font, fill=(*STATS_COLOR, 255),
        )
        if label:
            lw = draw.textlength(label, font=label_font)
            ly = top + value_h + STATS_LABEL_GAP
            draw.text(
                (center_x - lw / 2, ly),
                label, font=label_font, fill=(*STATS_COLOR, 255),
            )


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
            canvas = apply_film_look(crop_to_portrait(src)).convert("RGBA")
    except OSError as exc:
        print(f"   ⚠️  Image illisible ({cover.name}) : {exc}")
        return "failed"

    draw_centered_title(canvas, title)

    if STATS_ENABLED:
        meta = read_stats(bundle)
        if meta:
            draw_stats(canvas, meta)

    out_path = bundle / OUTPUT_NAME
    canvas.convert("RGB").save(out_path, "JPEG", quality=JPEG_QUALITY, optimize=True)
    rel = bundle.relative_to(CONTENT_DIR)
    print(f"   ✅ {rel}/{OUTPUT_NAME}  «{title}»")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Génère les images Instagram (portrait 3:4) des articles."
    )
    parser.add_argument(
        "-o", "--only",
        metavar="MOTIF",
        help=(
            "Ne (re)génère que les bundles dont le chemin (relatif à content/) "
            "contient ce motif, insensible à la casse. Ex. : --only \"Nordkapp\"."
        ),
    )
    args = parser.parse_args()

    print("🎨 Génération des images Instagram (portrait 3:4)")
    print("=" * 60)

    if not FONT_PATH.exists():
        print(f"❌ Police introuvable : {FONT_PATH}")
        sys.exit(1)
    if not CONTENT_DIR.exists():
        print(f"❌ Dossier content introuvable : {CONTENT_DIR}")
        sys.exit(1)

    bundles = find_leaf_bundles()
    if args.only:
        needle = args.only.lower()
        bundles = [
            b for b in bundles
            if needle in str(b.relative_to(CONTENT_DIR)).lower()
        ]
        if not bundles:
            print(f"❌ Aucun bundle ne correspond au motif : « {args.only} »")
            sys.exit(1)
    print(f"📦 {len(bundles)} article(s) / étape(s) détecté(s)\n")

    generated = 0
    for bundle in bundles:
        if generate_for_bundle(bundle):
            generated += 1

    print("\n" + "=" * 60)
    print(f"✅ {generated}/{len(bundles)} image(s) « {OUTPUT_NAME} » générée(s)")


if __name__ == "__main__":
    main()
