"""Which images a note embeds, so only those move with it into a vault."""

from lecture2notes.outputs import images


def test_a_wikilink_filename_with_spaces_is_kept_whole():
    # Frames are named after the lecture stem, which is usually its title.
    note = "![[frames/01-Essential Breast Pathology-0136.png]]\n![[b c-2.png|400]]"

    assert images.referenced_images(note) == [
        "01-Essential Breast Pathology-0136.png",
        "b c-2.png",
    ]


def test_a_markdown_embed_drops_its_title_and_decodes_the_path():
    note = '![x](frames/a%20b-1.png "slide one")\n![y](<frames/c d-2.png>)'

    assert images.referenced_images(note) == ["a b-1.png", "c d-2.png"]


def test_remote_and_non_image_embeds_are_ignored():
    note = "![](https://example.org/a.png)\n![[notes/other.md]]"

    assert images.referenced_images(note) == []
