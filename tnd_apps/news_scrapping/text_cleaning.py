import html
import re


MOJIBAKE_REPLACEMENTS = {
    "â€™": "'",
    "â€˜": "'",
    "â€œ": '"',
    "â€": '"',
    "â€“": "-",
    "â€”": "-",
    "â€¦": "...",
    "Â ": " ",
    "Â": "",
}

UNICODE_ESCAPE_REPLACEMENTS = {
    "\\u2018": "'",
    "\\u2019": "'",
    "\\u201c": '"',
    "\\u201d": '"',
    "\\u2013": "-",
    "\\u2014": "-",
    "\\u2026": "...",
    "\\xa0": " ",
}

CHAR_REPLACEMENTS = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u2032": "'",
    "\u0060": "'",
    "\u00b4": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u2033": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u2212": "-",
    "\u2026": "...",
    "\u00a0": " ",
    "\u200b": "",
    "\u200c": "",
    "\u200d": "",
    "\ufeff": "",
}


def clean_article_text(value, preserve_paragraphs=True):
    """Normalize scraped article text for app display."""
    if value is None:
        return ""

    text = str(value)
    if not text:
        return ""

    for bad, good in UNICODE_ESCAPE_REPLACEMENTS.items():
        text = text.replace(bad, good)

    previous = None
    for _ in range(5):
        if text == previous:
            break
        previous = text
        text = html.unescape(text)

    for bad, good in MOJIBAKE_REPLACEMENTS.items():
        text = text.replace(bad, good)

    text = text.translate(str.maketrans(CHAR_REPLACEMENTS))
    text = re.sub(r"[ \t\f\v]+", " ", text)

    if preserve_paragraphs:
        text = re.sub(r" *\r\n *| *\r *| *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
    else:
        text = re.sub(r"\s+", " ", text)

    return text.strip()
