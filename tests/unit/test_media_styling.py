"""Tests for optional Remote UI rich-text media metadata styling."""

from media_styling import (
    DEFAULT_MEDIA_ARTIST_STYLE,
    DEFAULT_MEDIA_TITLE_STYLE,
    apply_media_text_style,
    normalize_media_style_template,
)


def test_styling_disabled_preserves_original_text() -> None:
    assert (
        apply_media_text_style(
            "AC/DC & Friends",
            enabled=False,
            template="<b>{text}</b>",
            fallback_template=DEFAULT_MEDIA_TITLE_STYLE,
        )
        == "AC/DC & Friends"
    )


def test_styling_applies_user_template() -> None:
    assert (
        apply_media_text_style(
            "Now Playing",
            enabled=True,
            template='<u><font color="#865cff">{text}</font></u>',
            fallback_template=DEFAULT_MEDIA_TITLE_STYLE,
        )
        == '<u><font color="#865cff">Now Playing</font></u>'
    )


def test_metadata_is_escaped_before_inserting_into_rich_text() -> None:
    assert (
        apply_media_text_style(
            "A & <img src=bad>",
            enabled=True,
            template="<b>{text}</b>",
            fallback_template=DEFAULT_MEDIA_TITLE_STYLE,
        )
        == "<b>A &amp; &lt;img src=bad&gt;</b>"
    )


def test_invalid_template_falls_back_to_default() -> None:
    assert normalize_media_style_template("<b>constant</b>", DEFAULT_MEDIA_TITLE_STYLE) == DEFAULT_MEDIA_TITLE_STYLE
    assert (
        apply_media_text_style(
            "Song",
            enabled=True,
            template="<b>constant</b>",
            fallback_template=DEFAULT_MEDIA_TITLE_STYLE,
        )
        == "<b>Song</b>"
    )


def test_default_artist_style() -> None:
    assert (
        apply_media_text_style(
            "Artist",
            enabled=True,
            template=DEFAULT_MEDIA_ARTIST_STYLE,
            fallback_template=DEFAULT_MEDIA_ARTIST_STYLE,
        )
        == '<i><font color="#9ca3af">Artist</font></i>'
    )
