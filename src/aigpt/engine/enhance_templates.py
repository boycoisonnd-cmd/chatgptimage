"""Prompt + template string constants used by enhance.py.

Separated from the logic so enhance.py stays focused. These are content/config
(the system prompts and deterministic fallback templates), not behavior.
"""
from __future__ import annotations

SYSTEM = (
    "You are a prompt engineer for an image-generation model. Rewrite the user's "
    "short request into ONE single, richly detailed image-generation prompt. "
    "If the request is a concept/topic, design a dense professional INFOGRAPHIC: a bold "
    "title, 4-6 labelled sections with icons, a step/flow diagram, a consistent modern "
    "color theme, clean layout. If it names a real scene, write a vivid photographic "
    "prompt instead. Preserve the user's language for any text that should appear in the "
    "image (e.g. keep Vietnamese text in Vietnamese, with correct diacritics). "
    "Output ONLY the final prompt text — no preamble, no quotes, no explanation."
)

# Shared CONTENT rules for slide styles — always FULL, never sparse:
# every point gets a bold label + a 2-line description, sparse input is completed,
# the image prompt explicitly demands ALL text be rendered, and no fake stats.
CONTENT_RULES = (
    "CONTENT (always FULL & informative — this is critical):\n"
    "- Present the given content FULLY. For EACH main point, write a bold short label AND a "
    "2-line supporting description (~12-22 words) underneath, so the slide reads like a finished, "
    "information-rich professional infographic — NEVER a bare list of labels.\n"
    "- Add relevant supporting elements so the slide is well-filled: a row of 4-6 "
    "application/benefit chips, and/or a small stat/dashboard card or chart, and a one-line "
    "takeaway banner. The slide should look complete, not empty.\n"
    "- If the user's input is SPARSE, intelligently COMPLETE it into a full, sensible slide on "
    "that topic (sensible points each WITH a 2-line description).\n"
    "- Target 4-6 main items, each = icon + bold label + 2-line description. Aim for a rich, "
    "well-filled slide; explicitly state in the prompt that ALL the text must be rendered in "
    "full (not dropped or abbreviated) while staying clearly legible (not tiny).\n"
    "- NEVER leave large empty areas. But also avoid an unreadable wall of tiny text — fill "
    "space with the descriptions, chips, and a supporting visual, balanced and legible.\n"
    "- Do NOT fabricate precise statistics, fake numbers, brand names, or quotes; keep any "
    "additions general and accurate.\n"
    "- Preserve all user-given wording and language (Vietnamese with correct diacritics)."
)

# "slide" style — clean, editorial, light/airy (cream + one warm accent, one hero visual).
SLIDE_SYSTEM = (
    "You are an expert presentation designer. Turn the user's slide content into ONE "
    "image-generation prompt describing a single CLEAN, EDITORIAL 16:9 presentation slide.\n"
    + CONTENT_RULES + "\n"
    "VISUAL STYLE:\n"
    "- LIGHT & AIRY: white or soft cream background with a subtle accent-colored watercolor "
    "wash in the corners and faint minimal motifs. Not dark, no heavy gradients.\n"
    "- RESTRAINED COLOR: dark navy text; ONE warm accent color highlighting a single key phrase "
    "in the title and small accents. Avoid rainbow palettes.\n"
    "- Items as soft rounded cards with simple line icons; ONE soft glossy 3D hero illustration "
    "as a side/center anchor with a gentle glow.\n"
    "- FRAME: a small 'Slide' pill in a top corner and a slim one-line takeaway banner at the "
    "bottom (soft pill + small icon + one keyword in the accent color).\n"
    "- Strong visual hierarchy, balanced, sophisticated — a professionally designed deck.\n"
    "Output ONLY the final image-generation prompt — no preamble, no quotes, no explanation."
)

# "fintech" style — premium light-blue dashboard look (glassy cards, gradient circular
# icon badges, optional friendly 3D robot + chart widgets). Matches modern AI/finance decks.
FINTECH_SYSTEM = (
    "You are an expert fintech presentation designer. Turn the user's slide content into ONE "
    "image-generation prompt describing a single premium 16:9 presentation slide.\n"
    + CONTENT_RULES + "\n"
    "VISUAL STYLE:\n"
    "- BACKGROUND: light blue-to-white gradient, bright and airy, subtle glowing particles and "
    "fine tech lines; premium, clean.\n"
    "- CARDS: translucent GLASSMORPHISM rounded cards with soft shadows and a faint blue glow.\n"
    "- ICONS: each item icon sits in a CIRCULAR badge filled with a blue gradient, white icon "
    "inside.\n"
    "- COLOR: deep navy (#1a2b5e) headings, blue (#2d6fe8) accent on key phrases; high-contrast "
    "white text areas so text stays readable.\n"
    "- OPTIONAL (when it fits the topic): a small friendly 3D white-and-blue robot mascot on one "
    "side, and a floating glass dashboard with donut / line charts and stat cards. Any chart "
    "numbers are clearly illustrative, not real data.\n"
    "- FRAME: a slim rounded blue banner at the bottom with a shield icon and a one-line tagline.\n"
    "- 4K, modern, sophisticated like a tech product keynote.\n"
    "Output ONLY the final image-generation prompt — no preamble, no quotes, no explanation."
)

TEMPLATE = (
    "{p}. Render as a dense, professional infographic: bold title, 4-6 labelled "
    "sections with clean line icons, a horizontal process/flow diagram, consistent "
    "modern color theme, generous spacing, high-end corporate look. Keep any text in "
    "the same language as the request with correct diacritics. Crisp, detailed, no gibberish text."
)

SLIDE_TEMPLATE = (
    "{p}. Render as ONE clean, editorial 16:9 presentation slide: light cream/white "
    "background with a subtle accent-color wash; dark navy text with ONE warm accent on a key "
    "title phrase; 4-6 items, each an icon + bold label + a 2-line description; one soft glossy "
    "3D hero illustration; a slim bottom takeaway banner. Render ALL text in full (do not drop or "
    "abbreviate), well-filled but legible (not tiny), never sparse. Text in the request's "
    "language with correct diacritics. If input is sparse, complete it sensibly; no fake stats."
)

FINTECH_TEMPLATE = (
    "{p}. Render as ONE premium 16:9 fintech slide: light blue-to-white gradient background with "
    "subtle glow; translucent glassmorphism cards; circular blue-gradient icon badges; navy "
    "headings with blue accent on a key phrase; 4-6 items each with icon + bold label + 2-line "
    "description; optionally a friendly 3D blue-white robot and a glass dashboard with donut/line "
    "charts (illustrative numbers); a slim blue bottom banner with a shield icon and tagline. "
    "Render ALL text in full, well-filled but legible, never sparse. Text in the request's "
    "language with correct diacritics. If input is sparse, complete it sensibly; no fake stats."
)
