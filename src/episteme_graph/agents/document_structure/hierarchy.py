"""SectionHierarchyBuilder: 見出しブロック群からセクション木を構築し、各ブロックをセクションに割り当てる。"""
from __future__ import annotations

from .schema import Section, TypedBlock

_HEADING_TYPES = frozenset({"section_heading", "subsection_heading"})


class SectionHierarchyBuilder:
    """TypedBlock のリストからセクション階層を構築する。

    副作用として block.section_id を in-place で書き込む。
    """

    def build(self, blocks: list[TypedBlock], document_id: str) -> list[Section]:
        sections: list[Section] = []
        # stack: list of (level, section_id)
        stack: list[tuple[int, str]] = []
        section_counter = 0

        # Pass 1: create Section objects from heading blocks
        for block in blocks:
            if block.block_type not in _HEADING_TYPES:
                continue

            level = 1 if block.block_type == "section_heading" else 2
            section_counter += 1
            section_id = f"{document_id}:sec_{section_counter}"

            # Pop stack until we find a parent with smaller level
            while stack and stack[-1][0] >= level:
                stack.pop()

            parent_id = stack[-1][1] if stack else None
            stack.append((level, section_id))

            section = Section(
                section_id=section_id,
                title=block.text.strip(),
                level=level,
                order=section_counter,
                page_start=block.page,
                parent_section_id=parent_id,
            )
            sections.append(section)
            block.section_id = section_id  # heading block itself belongs to its section

        # Compute page_end for each section
        self._set_page_ends(sections, blocks)

        # Pass 2: assign non-heading blocks to the current section
        self._assign_to_sections(blocks, sections)

        return sections

    @staticmethod
    def _set_page_ends(sections: list[Section], blocks: list[TypedBlock]) -> None:
        if not sections:
            return
        max_page = max((b.page for b in blocks), default=1)
        for i, section in enumerate(sections):
            next_section = sections[i + 1] if i + 1 < len(sections) else None
            section.page_end = next_section.page_start if next_section else max_page

    @staticmethod
    def _assign_to_sections(blocks: list[TypedBlock], sections: list[Section]) -> None:
        if not sections:
            return

        # Build a quick lookup: (title, page) → section_id
        heading_map: dict[tuple[str, int], str] = {
            (s.title, s.page_start): s.section_id for s in sections
        }

        current_section_id: str | None = None
        for block in blocks:
            if block.block_type in _HEADING_TYPES:
                key = (block.text.strip(), block.page)
                current_section_id = heading_map.get(key, current_section_id)
                block.section_id = current_section_id
            else:
                if block.section_id is None:
                    block.section_id = current_section_id
