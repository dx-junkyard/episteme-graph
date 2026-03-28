"""OSL .isom format serialization.

Generates .isom files with YAML front-matter and DSL body from PaperStructure.
Supports both single-paper and batch (multi-paper) output.

File naming: temp_[source_id_hash].isom (temporary names for OSL pipeline).
Output directory: output/incoming/
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from core.config import get_settings
from core.schema import PaperStructure

logger = logging.getLogger(__name__)

# Default output directory for batch .isom files
_DEFAULT_OUTPUT_DIR = get_settings().isom_output_dir


def _source_id(structure: PaperStructure) -> str:
    """Derive a source_id (DOI-style or arXiv URL) from PaperStructure."""
    paper_id = structure.paper_id
    if paper_id:
        return f"https://arxiv.org/abs/{paper_id}"
    return ""


def _hash_source_id(source_id: str) -> str:
    """Create a short hash from source_id for temp filename."""
    return hashlib.sha256(source_id.encode()).hexdigest()[:12]


def _extract_patterns(structure: PaperStructure) -> list[str]:
    """Extract pattern names from core edges for the YAML front-matter."""
    patterns: list[str] = []
    for edge in structure.abstract_structure.edges:
        if edge.is_core:
            label = f"{edge.core_predicate.value}:{edge.domain_verb}"
            if label not in patterns:
                patterns.append(label)
    return patterns


def serialize_isom(structure: PaperStructure) -> str:
    """Serialize a PaperStructure into .isom format (YAML front-matter + DSL body).

    The generated content intentionally omits any engine branding.

    Parameters
    ----------
    structure:
        The PaperStructure to serialize.

    Returns
    -------
    str
        Complete .isom file content.
    """
    source_id = _source_id(structure)
    authors = structure.authors or []
    year = structure.year if structure.year else ""
    domain = structure.domain or ""
    patterns = _extract_patterns(structure)

    # --- YAML front-matter ---
    lines: list[str] = ["---"]
    lines.append(f'source_id: "{source_id}"')
    lines.append(f'title: "{_yaml_escape(structure.title)}"')
    lines.append(f"authors: [{', '.join(_yaml_quote(a) for a in authors)}]")
    lines.append(f"year: {year}")
    lines.append(f'domain: "{_yaml_escape(domain)}"')
    lines.append(f"patterns: [{', '.join(_yaml_quote(p) for p in patterns)}]")
    lines.append("---")
    lines.append("")

    # --- DSL body (SMILES DSL string) ---
    if structure.abstract_structure.smiles_dsl:
        lines.append(structure.abstract_structure.smiles_dsl)
        lines.append("")

    return "\n".join(lines)


def _yaml_escape(value: str) -> str:
    """Escape double quotes in YAML string values."""
    return value.replace('"', '\\"')


def _yaml_quote(value: str) -> str:
    """Quote a value for YAML list format."""
    return f'"{_yaml_escape(value)}"'


def write_isom_file(
    structure: PaperStructure,
    output_dir: str | None = None,
) -> str:
    """Write a single PaperStructure as a .isom file to the output directory.

    Parameters
    ----------
    structure:
        The PaperStructure to write.
    output_dir:
        Target directory. Defaults to output/incoming/.

    Returns
    -------
    str
        The absolute path to the written .isom file.
    """
    target_dir = Path(output_dir or _DEFAULT_OUTPUT_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)

    source_id = _source_id(structure)
    filename = f"temp_{_hash_source_id(source_id)}.isom"
    filepath = target_dir / filename

    content = serialize_isom(structure)
    filepath.write_text(content, encoding="utf-8")

    logger.info("Wrote .isom file: %s", filepath)
    return str(filepath.resolve())


def write_batch_isom(
    structures: list[PaperStructure],
    output_dir: str | None = None,
) -> list[str]:
    """Write multiple PaperStructures as individual .isom files.

    Each paper is written to a separate file in the output directory
    using the naming convention temp_[source_id_hash].isom.

    Parameters
    ----------
    structures:
        List of PaperStructure objects to write.
    output_dir:
        Target directory. Defaults to output/incoming/.

    Returns
    -------
    list[str]
        List of absolute paths to the written .isom files.
    """
    paths: list[str] = []
    for structure in structures:
        path = write_isom_file(structure, output_dir=output_dir)
        paths.append(path)
    return paths
