"""Remote UI rich-text styling helpers for media metadata."""

from html import escape

DEFAULT_MEDIA_TITLE_STYLE = "<b>{text}</b>"
DEFAULT_MEDIA_ARTIST_STYLE = '<i><font color="#9ca3af">{text}</font></i>'
TEXT_PLACEHOLDER = "{text}"


def normalize_media_style_template(template: str | None, fallback: str) -> str:
    """Return a usable rich-text template containing the metadata placeholder."""
    normalized = (template or "").strip()
    return normalized if TEXT_PLACEHOLDER in normalized else fallback


def apply_media_text_style(
    text: str | None,
    *,
    enabled: bool,
    template: str | None,
    fallback_template: str,
) -> str:
    """Apply an opt-in Qt rich-text template to a media metadata value."""
    if not text:
        return ""
    if not enabled:
        return text
    chosen = normalize_media_style_template(template, fallback_template)
    safe_text = escape(text, quote=False)
    return chosen.replace(TEXT_PLACEHOLDER, safe_text)
