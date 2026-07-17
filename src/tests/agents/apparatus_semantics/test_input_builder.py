"""Tests for ApparatusSemanticsInputBuilder.

Covers the non-LLM assembly helpers in input_builder.py that turn a
``FigureImageInput`` + optional ``CartridgeContext`` into the plain-data
pieces ``prompt.py`` renders into messages: in-figure label hints,
abbreviation dictionaries, nearby-text backstop capping, cartridge vocabulary
hints, and the image payload (mime-type detection + base64 encoding).
This does not re-test anything already covered by test_agent.py /
test_label_grounding.py (which exercise these helpers indirectly through
``ApparatusSemanticsAgent.run()``) — here each helper is called directly and
in isolation. No external API is called anywhere in this file.
"""
from __future__ import annotations

import base64

from episteme_graph.agents.apparatus_semantics.input_builder import (
    ApparatusSemanticsInputBuilder,
)
from episteme_graph.agents.apparatus_semantics.schema import (
    CartridgeContext,
    FigureImageInput,
)

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-bytes"
_JPEG_BYTES = b"\xff\xd8\xff" + b"fake-jpeg-bytes"

BUILDER = ApparatusSemanticsInputBuilder()


def _figure(
    figure_id: str = "fig_1",
    inner_labels=None,
    abbreviations=None,
    nearby=None,
    image_bytes=None,
    guidance_text: str = "",
    focus_bbox_rel=None,
    focus_image_bytes=None,
    focus_label_texts=None,
) -> FigureImageInput:
    return FigureImageInput(
        figure_id=figure_id,
        figure_key=figure_id,
        figure_label="Figure 1",
        caption_text="A schematic of the setup.",
        image_bytes=image_bytes,
        nearby_text=nearby if nearby is not None else [],
        inner_labels=inner_labels if inner_labels is not None else [],
        abbreviations=abbreviations if abbreviations is not None else {},
        guidance_text=guidance_text,
        focus_bbox_rel=focus_bbox_rel,
        focus_image_bytes=focus_image_bytes,
        focus_label_texts=focus_label_texts if focus_label_texts is not None else [],
    )


# ---------------------------------------------------------------------------
# build_inner_label_hints
# ---------------------------------------------------------------------------


def test_build_inner_label_hints_strips_whitespace():
    figure = _figure(inner_labels=[{"text": "  EOM  ", "bbox": [1, 2, 3, 4]}])
    assert BUILDER.build_inner_label_hints(figure) == ["EOM"]


def test_build_inner_label_hints_preserves_order_and_dedupes():
    figure = _figure(inner_labels=[
        {"text": "EOM"},
        {"text": "PBS"},
        {"text": "EOM"},  # duplicate, later occurrence dropped
        {"text": "ECDL"},
    ])
    assert BUILDER.build_inner_label_hints(figure) == ["EOM", "PBS", "ECDL"]


def test_build_inner_label_hints_skips_non_dict_elements():
    figure = _figure(inner_labels=[{"text": "EOM"}, "not_a_dict", 42, None])
    assert BUILDER.build_inner_label_hints(figure) == ["EOM"]


def test_build_inner_label_hints_skips_empty_or_whitespace_only_text():
    figure = _figure(inner_labels=[
        {"text": "EOM"},
        {"text": ""},
        {"text": "   "},
        {},  # no "text" key at all
    ])
    assert BUILDER.build_inner_label_hints(figure) == ["EOM"]


def test_build_inner_label_hints_empty_input_returns_empty_list():
    figure = _figure(inner_labels=[])
    assert BUILDER.build_inner_label_hints(figure) == []


def test_build_inner_label_hints_truncates_at_60():
    inner_labels = [{"text": f"L{i}"} for i in range(70)]
    figure = _figure(inner_labels=inner_labels)
    hints = BUILDER.build_inner_label_hints(figure)
    assert len(hints) == 60
    assert hints == [f"L{i}" for i in range(60)]


# ---------------------------------------------------------------------------
# build_abbreviations
# ---------------------------------------------------------------------------


def test_build_abbreviations_normalizes_str_and_strips_whitespace():
    figure = _figure(abbreviations={" EOM ": " electro-optic modulator "})
    assert BUILDER.build_abbreviations(figure) == {"EOM": "electro-optic modulator"}


def test_build_abbreviations_empty_dict_returns_empty_dict():
    figure = _figure(abbreviations={})
    assert BUILDER.build_abbreviations(figure) == {}


def test_build_abbreviations_skips_empty_key_or_empty_value():
    figure = _figure(abbreviations={
        "": "orphan value",
        "ORPHAN_KEY": "",
        "   ": "whitespace-only key",
        "PBS": "polarizing beam splitter",
    })
    assert BUILDER.build_abbreviations(figure) == {"PBS": "polarizing beam splitter"}


def test_build_abbreviations_caps_at_40():
    figure = _figure(abbreviations={f"K{i}": f"V{i}" for i in range(50)})
    abbreviations = BUILDER.build_abbreviations(figure)
    assert len(abbreviations) == 40
    assert abbreviations == {f"K{i}": f"V{i}" for i in range(40)}


# ---------------------------------------------------------------------------
# build_nearby_text
# ---------------------------------------------------------------------------


def test_build_nearby_text_strips_each_item():
    figure = _figure(nearby=["  hello world  "])
    assert BUILDER.build_nearby_text(figure) == ["hello world"]


def test_build_nearby_text_excludes_whitespace_only_items():
    figure = _figure(nearby=["valid text", "", "   ", "\n\t"])
    assert BUILDER.build_nearby_text(figure) == ["valid text"]


def test_build_nearby_text_empty_input_returns_empty_list():
    figure = _figure(nearby=[])
    assert BUILDER.build_nearby_text(figure) == []


def test_build_nearby_text_caps_at_16_items():
    figure = _figure(nearby=[f"text item {i}" for i in range(20)])
    texts = BUILDER.build_nearby_text(figure)
    assert len(texts) == 16
    assert texts == [f"text item {i}" for i in range(16)]


def test_build_nearby_text_caps_each_item_at_1500_chars():
    figure = _figure(nearby=["x" * 2000])
    texts = BUILDER.build_nearby_text(figure)
    assert len(texts) == 1
    assert len(texts[0]) == 1500
    assert texts[0] == "x" * 1500


# ---------------------------------------------------------------------------
# build_cartridge_hints
# ---------------------------------------------------------------------------


def test_build_cartridge_hints_returns_empty_dict_for_none_cartridge():
    assert BUILDER.build_cartridge_hints(None) == {}


def test_build_cartridge_hints_extracts_component_and_relation_types():
    cartridge = CartridgeContext(
        cartridge_id="particle_physics",
        ontology={},
        validation_rules={},
        aliases={"EOM": "electro-optic modulator"},
        extraction_hints={
            "component_types": ["instrument", "part"],
            "relation_types": ["optical_path"],
        },
    )
    hints = BUILDER.build_cartridge_hints(cartridge)
    assert hints == {
        "component_types": ["instrument", "part"],
        "relation_types": ["optical_path"],
        "aliases": {"EOM": "electro-optic modulator"},
    }


def test_build_cartridge_hints_non_dict_extraction_hints_does_not_crash():
    cartridge = CartridgeContext(
        cartridge_id="particle_physics",
        ontology={},
        validation_rules={},
        aliases=None,
        extraction_hints=["not", "a", "dict"],
    )
    hints = BUILDER.build_cartridge_hints(cartridge)
    assert hints == {"component_types": [], "relation_types": [], "aliases": {}}


def test_build_cartridge_hints_missing_extraction_hints_defaults_to_empty_lists():
    cartridge = CartridgeContext(
        cartridge_id="particle_physics",
        ontology={},
        validation_rules={},
        extraction_hints=None,
    )
    hints = BUILDER.build_cartridge_hints(cartridge)
    assert hints == {"component_types": [], "relation_types": [], "aliases": {}}


def test_build_cartridge_hints_aliases_default_to_empty_dict_when_none():
    cartridge = CartridgeContext(
        cartridge_id="particle_physics",
        ontology={},
        validation_rules={},
        aliases=None,
        extraction_hints={"component_types": [], "relation_types": []},
    )
    hints = BUILDER.build_cartridge_hints(cartridge)
    assert hints["aliases"] == {}


# ---------------------------------------------------------------------------
# build_image_payload
# ---------------------------------------------------------------------------


def test_build_image_payload_returns_none_when_no_image_bytes():
    figure = _figure(image_bytes=None)
    assert BUILDER.build_image_payload(figure) is None


def test_build_image_payload_detects_png_mime_type():
    figure = _figure(image_bytes=_PNG_BYTES)
    payload = BUILDER.build_image_payload(figure)
    assert payload["mime_type"] == "image/png"


def test_build_image_payload_detects_jpeg_mime_type():
    figure = _figure(image_bytes=_JPEG_BYTES)
    payload = BUILDER.build_image_payload(figure)
    assert payload["mime_type"] == "image/jpeg"


def test_build_image_payload_unrecognized_bytes_default_to_png_mime_type():
    figure = _figure(image_bytes=b"not-a-real-image-magic-header")
    payload = BUILDER.build_image_payload(figure)
    assert payload["mime_type"] == "image/png"


def test_build_image_payload_base64_encodes_bytes_correctly():
    figure = _figure(image_bytes=_PNG_BYTES)
    payload = BUILDER.build_image_payload(figure)
    assert base64.b64decode(payload["data_base64"]) == _PNG_BYTES


# ---------------------------------------------------------------------------
# build_image_payloads (guided_figure_reanalysis_design.md §6-2)
# ---------------------------------------------------------------------------


def test_build_image_payloads_returns_empty_list_when_no_original_image():
    figure = _figure(image_bytes=None, focus_image_bytes=_PNG_BYTES)
    # No original image -> no list at all, even if a focus crop is present
    # (a crop without the original never reaches the LLM as a standalone call).
    assert BUILDER.build_image_payloads(figure) == []


def test_build_image_payloads_single_image_without_focus():
    figure = _figure(image_bytes=_PNG_BYTES)
    payloads = BUILDER.build_image_payloads(figure)
    assert len(payloads) == 1
    assert base64.b64decode(payloads[0]["data_base64"]) == _PNG_BYTES


def test_build_image_payloads_two_images_with_focus_crop():
    figure = _figure(image_bytes=_PNG_BYTES, focus_image_bytes=_JPEG_BYTES)
    payloads = BUILDER.build_image_payloads(figure)
    assert len(payloads) == 2
    # first element is always the original figure image...
    assert payloads[0]["mime_type"] == "image/png"
    assert base64.b64decode(payloads[0]["data_base64"]) == _PNG_BYTES
    # ...second is the magnified focus crop
    assert payloads[1]["mime_type"] == "image/jpeg"
    assert base64.b64decode(payloads[1]["data_base64"]) == _JPEG_BYTES


def test_build_image_payload_singular_returns_first_of_payloads():
    """Backward-compat wrapper: same first element, never the focus crop."""
    figure = _figure(image_bytes=_PNG_BYTES, focus_image_bytes=_JPEG_BYTES)
    assert BUILDER.build_image_payload(figure) == BUILDER.build_image_payloads(figure)[0]


# ---------------------------------------------------------------------------
# build_guidance (guided_figure_reanalysis_design.md §6-2)
# ---------------------------------------------------------------------------


def test_build_guidance_returns_empty_dict_when_nothing_supplied():
    figure = _figure()
    assert BUILDER.build_guidance(figure) == {}


def test_build_guidance_normalizes_hint_text_only():
    figure = _figure(guidance_text="  look at the left box  ")
    guidance = BUILDER.build_guidance(figure)
    assert guidance == {
        "hint_text": "look at the left box",
        "focus_bbox_rel": None,
        "focus_label_texts": [],
        "has_focus_image": False,
    }


def test_build_guidance_includes_focus_bbox_and_image_flag():
    figure = _figure(
        focus_bbox_rel=[0.1, 0.2, 0.5, 0.6],
        focus_image_bytes=_PNG_BYTES,
        focus_label_texts=["EOM", "PBS"],
    )
    guidance = BUILDER.build_guidance(figure)
    assert guidance["focus_bbox_rel"] == [0.1, 0.2, 0.5, 0.6]
    assert guidance["has_focus_image"] is True
    assert guidance["focus_label_texts"] == ["EOM", "PBS"]
    assert guidance["hint_text"] == ""


def test_build_guidance_present_when_only_focus_bbox_given():
    """A bbox with no hint text is still meaningful guidance (not {})."""
    figure = _figure(focus_bbox_rel=[0.0, 0.0, 1.0, 1.0])
    assert BUILDER.build_guidance(figure) != {}


def test_build_guidance_caps_hint_text_at_2000_chars():
    figure = _figure(guidance_text="x" * 3000)
    guidance = BUILDER.build_guidance(figure)
    assert len(guidance["hint_text"]) == 2000


def test_build_guidance_strips_whitespace_only_focus_label_texts():
    figure = _figure(focus_bbox_rel=[0.1, 0.1, 0.2, 0.2], focus_label_texts=["  ", "EOM", ""])
    guidance = BUILDER.build_guidance(figure)
    assert guidance["focus_label_texts"] == ["EOM"]


def test_build_guidance_empty_focus_bbox_list_is_treated_as_absent():
    figure = _figure(focus_bbox_rel=[])
    assert BUILDER.build_guidance(figure) == {}
