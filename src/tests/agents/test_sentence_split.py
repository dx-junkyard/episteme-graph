"""IK-0393: abbreviation-aware sentence splitting shared by the pipeline.

The naive ``(?<=[.!?])\\s+`` split in ClaimObjectBuilder cut
"The ACF analysis (Houde et al. 2009), extends ..." into
"The ACF analysis (Houde et al." + "2009), extends ...".
"""

from __future__ import annotations

import pathlib

import pytest

from episteme_graph.agents.claim_object_builder.builder import ClaimObjectBuilder
from episteme_graph.agents.evidence_registry.builder import split_sentences_with_offsets
from episteme_graph.agents.sentence_split import (
    NON_TERMINAL_ABBREVIATIONS,
    split_sentence_spans,
    split_sentences,
)

_HOUDE = (
    "The ACF analysis (Houde et al. 2009), extends the SF analysis by accounting "
    "for the effects of signal integration along the line of sight."
)


def test_et_al_with_year_is_not_a_boundary():
    assert split_sentences(_HOUDE) == [_HOUDE]


@pytest.mark.parametrize(
    "text",
    [
        "As shown in Fig. 3 the field is ordered.",
        "See Figs. 3 and 4 for the maps.",
        "Using Eq. 5 we obtain the ratio.",
        "Eqs. 4-5 give the mass.",
        "In Sec. 2 we describe the data.",
        "Following Ref. 12 we adopt Q.",
        "Turbulence vs. gravity sets the scale.",
        "Some tracers, e.g. dust, are used.",
        "The tail, i.e. the diffuse part, is aligned.",
        "This agrees with earlier work (cf. the Planck map).",
        "Core No. 6 is the densest.",
        "The DCF method (Davis 1951. Chandrasekhar & Fermi 1953) is used.",
    ],
)
def test_common_abbreviations_do_not_split(text):
    assert split_sentences(text) == [text]


def test_real_boundaries_still_split():
    text = "The filament is dense. It is magnetically subcritical! Is it stable? 磁場は強い。 次の文。"
    assert split_sentences(text) == [
        "The filament is dense.",
        "It is magnetically subcritical!",
        "Is it stable?",
        "磁場は強い。",
        "次の文。",
    ]


def test_number_period_outside_parentheses_is_a_boundary():
    assert split_sentences("The value is 3. The next one is 4.") == [
        "The value is 3.",
        "The next one is 4.",
    ]


def test_offsets_index_into_the_original_text():
    text = "  First one (Houde et al. 2009) holds.  Second one."
    spans = split_sentence_spans(text)
    assert [text[a:b] for a, b in spans] == [
        "First one (Houde et al. 2009) holds.",
        "Second one.",
    ]


def test_evidence_registry_uses_the_shared_splitter():
    assert split_sentences_with_offsets(_HOUDE) == split_sentence_spans(_HOUDE)


def test_claim_builder_atomicity_ignores_et_al():
    atomicity, _reason = ClaimObjectBuilder._analyze_atomicity(_HOUDE)
    assert atomicity == "atomic"


def test_claim_builder_split_does_not_cut_at_et_al():
    two = _HOUDE + " The SF analysis alone overestimates the dispersion."
    parts = ClaimObjectBuilder._split_into_atomic(two)
    assert parts, parts
    assert not any(part.rstrip().endswith("et al.") for part in parts)
    assert not any(part.lstrip().startswith("2009)") for part in parts)


def test_required_abbreviations_are_listed():
    for abbr in ("et al", "al", "fig", "figs", "eq", "eqs", "sec", "ref", "vs", "e.g", "i.e", "cf", "no"):
        assert abbr in NON_TERMINAL_ABBREVIATIONS


def test_no_naive_sentence_regex_left_in_claim_builder():
    root = pathlib.Path(__file__).resolve().parents[2] / "episteme_graph" / "agents"
    source = (root / "claim_object_builder" / "builder.py").read_text(encoding="utf-8")
    assert 're.split(r"(?<=[.!?])\\s+"' not in source
    assert 're.split(r"[.!?]+\\s+"' not in source
