"""Write the labdex logo files from one definition of the mark.

    pip install fonttools brotli
    python tools/brand.py

The mark is a lantern, which the theme is built around. The wordmark is set
in Red Hat Display 600 and written as outlines, so the SVGs look the same
without the font installed. Output goes to docs/assets/ (docs, README, social
preview) and frontend/src/assets/mark.svg (the app).
"""
from pathlib import Path

ROOT = Path(__file__).parents[2]
FONT = ROOT / "docs/assets/fonts/red-hat-display-latin-600-normal.woff2"
DOCS = ROOT / "docs/assets"
APP = ROOT / "frontend/src/assets"

VIOLET_TOP, VIOLET_BOTTOM = "#8b5cf6", "#6d28d9"
GLOW, GLOW_CORE = "#fbbf24", "#fffbeb"
INK_LIGHT, INK_DARK = "#19171f", "#ece9f2"
WORD = "labdex"
TAGLINE = "See the whole lab."

# The lantern on a 32x32 grid: handle, cap, glass, flame, base. The glass is a frame
# around a light, the nested-zones idea from the earlier mark: what's inside is lit.
HANDLE = "M13.2 7.6a2.8 2.8 0 0 1 5.6 0"
CAP = "M11.4 8.2h9.2l2.4 3H9z"
GLASS = "M10.2 11.2h11.6v12.3a1.8 1.8 0 0 1-1.8 1.8h-8a1.8 1.8 0 0 1-1.8-1.8z"
FLAME = "M16 13.4c1 1.8 3.5 3.3 3.5 6.3a3.5 3.5 0 0 1-7 0c0-1.8 1-2.9 2-3.9.2 1 .6 1.6 1.2 1.8-.2-1.6-.2-3 .3-4.2z"
CORE = "M16 18.2c.6.7 1.4 1.3 1.4 2.3a1.4 1.4 0 0 1-2.8 0c0-.9.7-1.6 1.4-2.3z"
BASE = "M8.6 25.6h14.8"


def lantern(line: str, uid: str) -> str:
    """The lantern alone, drawn in `line`, with its glow (ids get `uid` so marks can share a page)."""
    return (
        f'<radialGradient id="g{uid}" cx="16" cy="19" r="10" gradientUnits="userSpaceOnUse">'
        f'<stop offset="0" stop-color="#fde68a" stop-opacity=".85"/><stop offset=".4" stop-color="{GLOW}" stop-opacity=".3"/>'
        f'<stop offset="1" stop-color="{GLOW}" stop-opacity="0"/></radialGradient>'
        f'<circle cx="16" cy="19" r="10" fill="url(#g{uid})"/>'
        f'<path d="{HANDLE}" fill="none" stroke="{line}" stroke-width="1.8" stroke-linecap="round"/>'
        f'<path d="{CAP}" fill="{line}"/>'
        f'<path d="{GLASS}" fill="none" stroke="{line}" stroke-width="2"/>'
        f'<path d="{FLAME}" fill="{GLOW}"/><path d="{CORE}" fill="{GLOW_CORE}"/>'
        f'<path d="{BASE}" stroke="{line}" stroke-width="2.2" stroke-linecap="round"/>'
    )


def tile(uid: str = "") -> str:
    """The app icon: the white lantern on a violet tile."""
    return (
        f'<linearGradient id="b{uid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{VIOLET_TOP}"/>'
        f'<stop offset="1" stop-color="{VIOLET_BOTTOM}"/></linearGradient>'
        f'<rect width="32" height="32" rx="7" fill="url(#b{uid})"/>' + lantern("#fff", uid)
    )


def svg(view: str, body: str, size: str = "") -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}"{size}><title>labdex</title>{body}</svg>\n'


def wordmark(height: float, text: str = WORD) -> tuple[str, float]:
    """`text` as one outline path, baseline at 0 and cap height scaled to `height`; returns (path, width)."""
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont

    font = TTFont(FONT)
    glyphs, cmap, hmtx = font.getGlyphSet(), font.getBestCmap(), font["hmtx"]
    scale = height / font["OS/2"].sCapHeight
    pen, x = SVGPathPen(glyphs, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip(".")), 0.0
    for ch in text:
        name = cmap[ord(ch)]
        glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x, 0)))
        x += hmtx[name][0] * scale
    return pen.getCommands(), x


def logo(ink: str) -> str:
    """Mark and wordmark side by side, for the README and anything wide."""
    path, width = wordmark(22)
    w = round(48 + width + 4)
    return svg(f"0 0 {w} 44", f'<g transform="translate(0 2) scale(1.25)">{tile()}</g>'
               f'<path transform="translate(50 33)" fill="{ink}" d="{path}"/>', f' width="{w * 2}" height="88"')


def social() -> str:
    """GitHub's social preview, 1280x640: mark, name and what the suite is for."""
    path, width = wordmark(96)
    tag, tag_w = wordmark(26, TAGLINE)
    x = (1280 - (220 + 48 + width)) / 2
    return svg("0 0 1280 640",
               '<radialGradient id="sky" cx="640" cy="300" r="760" gradientUnits="userSpaceOnUse">'
               '<stop offset="0" stop-color="#2a1f45"/><stop offset="1" stop-color="#121119"/></radialGradient>'
               '<rect width="1280" height="640" fill="url(#sky)"/>'
               f'<g transform="translate({x:.1f} 200) scale(6.875)">{tile("s")}</g>'
               f'<path transform="translate({x + 268:.1f} 362)" fill="{INK_DARK}" d="{path}"/>'
               f'<path transform="translate({(1280 - tag_w) / 2:.1f} 520)" fill="#b8a4fb" d="{tag}"/>')


def main() -> None:
    files = {
        APP / "mark.svg": svg("0 0 32 32", tile()),
        DOCS / "labdex-mark.svg": svg("0 0 32 32", tile()),
        DOCS / "labdex-mark-white.svg": svg("0 0 32 32", lantern("#fff", "")),  # on the docs' violet header
        DOCS / "labdex-logo-light.svg": logo(INK_LIGHT),
        DOCS / "labdex-logo-dark.svg": logo(INK_DARK),
        DOCS / "labdex-social.svg": social(),
    }
    for p, text in files.items():
        p.write_text(text)
        print(p.relative_to(ROOT))


if __name__ == "__main__":
    main()
