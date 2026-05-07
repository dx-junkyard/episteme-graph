"""Smoke-test equation vision OCR against a cropped PDF equation candidate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .image_extractor import EquationImageExtractor
from .llm_client import EquationSemanticsLLMClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe whether the configured LLM can read equation images.")
    parser.add_argument("--pdf", required=True, help="Source PDF path.")
    parser.add_argument("--candidates", required=True, help="equation_candidates.json path.")
    parser.add_argument("--candidate-id", help="Candidate id to probe. Defaults to the first accepted/provisional candidate.")
    parser.add_argument("--model", help="Override analysis model.")
    args = parser.parse_args()

    candidates = _load_candidates(Path(args.candidates))
    candidate = _select_candidate(candidates, args.candidate_id)
    image = EquationImageExtractor().crop_candidate(args.pdf, candidate.get("source_location"))
    if not image:
        print(json.dumps({
            "ok": False,
            "reason": "equation_image_unavailable",
            "candidate_id": candidate.get("candidate_id"),
        }, ensure_ascii=False, indent=2))
        return

    messages = [
        {
            "role": "system",
            "content": (
                "You are a math OCR probe. Return JSON only. Read the attached equation image "
                "and transcribe the equation to LaTeX. Do not use the PDF text layer."
            ),
        },
        {
            "role": "user",
            "content": (
                "Return this shape exactly: "
                '{"latex": string|null, "plain_text": string|null, "confidence": number, "notes": string}.'
            ),
        },
    ]
    raw = EquationSemanticsLLMClient(model=args.model).generate(messages, image=image.__dict__)
    print(json.dumps({
        "ok": bool(raw.get("_vision_ocr", {}).get("used")),
        "candidate_id": candidate.get("candidate_id"),
        "raw_text_from_pdf_layer": candidate.get("raw_text"),
        "vision_ocr": raw.get("_vision_ocr"),
        "latex": raw.get("latex"),
        "plain_text": raw.get("plain_text"),
        "confidence": raw.get("confidence"),
        "notes": raw.get("notes"),
    }, ensure_ascii=False, indent=2))


def _load_candidates(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        candidates = payload.get("candidates", [])
    else:
        candidates = payload
    if not isinstance(candidates, list) or not candidates:
        raise SystemExit(f"No candidates found: {path}")
    return [c for c in candidates if isinstance(c, dict)]


def _select_candidate(candidates: list[dict[str, Any]], candidate_id: str | None) -> dict[str, Any]:
    if candidate_id:
        for candidate in candidates:
            if candidate.get("candidate_id") == candidate_id:
                return candidate
        raise SystemExit(f"Candidate not found: {candidate_id}")
    for candidate in candidates:
        if candidate.get("acceptance_status") in {"accepted", "provisional"}:
            return candidate
    return candidates[0]


if __name__ == "__main__":
    main()
