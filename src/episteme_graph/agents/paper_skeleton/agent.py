"""PaperSkeletonAgent: DocumentStructureResult から論文 backbone を仮説化する。

design: LLM-first / cartridge-aware (not cartridge-dependent)
- DocumentStructureAgent の出力を受け取り、LLM で backbone を生成
- cartridge がなくても単独で動作する
"""
from __future__ import annotations

import logging

from episteme_graph.agents.document_structure.schema import DocumentStructureResult

from .cartridge_loader import CartridgeLoader
from .input_builder import SkeletonInputBuilder
from .llm_client import PaperSkeletonLLMClient
from .prompt import PaperSkeletonPromptFactory
from .repair import PaperSkeletonRepairer, _parse_raw
from .schema import CartridgeContext, PaperSkeletonResult
from .validator import PaperSkeletonValidator

logger = logging.getLogger(__name__)


class PaperSkeletonAgent:
    """DocumentStructureResult を受け取り PaperSkeletonResult を返すエージェント。

    Parameters
    ----------
    cartridge_base_dir:
        カートリッジの検索ベースディレクトリ。None の場合は CartridgeLoader のデフォルト。
    llm_model:
        使用する LLM モデル名。None の場合は環境変数 LLM_ANALYSIS_MODEL を使用。
    """

    def __init__(
        self,
        cartridge_base_dir: str | None = None,
        llm_model: str | None = None,
    ) -> None:
        self._cartridge_loader = CartridgeLoader(cartridge_base_dir)
        self._input_builder = SkeletonInputBuilder()
        self._prompt_factory = PaperSkeletonPromptFactory()
        self._llm_client = PaperSkeletonLLMClient(model=llm_model)
        self._validator = PaperSkeletonValidator()
        self._repairer = PaperSkeletonRepairer()

    def run(
        self,
        structure: DocumentStructureResult,
        cartridge_id: str | None = None,
        config: dict | None = None,
    ) -> PaperSkeletonResult:
        """DocumentStructureResult から骨格仮説を生成する。

        Parameters
        ----------
        structure:
            DocumentStructureAgent の出力。
        cartridge_id:
            使用するカートリッジ ID。None の場合はカートリッジなしで動作。
        config:
            オプション設定（max_sections: int など）。
        """
        # Step 1: Load cartridge (optional)
        cartridge: CartridgeContext | None = None
        if cartridge_id:
            try:
                cartridge = self._cartridge_loader.load(cartridge_id)
            except FileNotFoundError:
                logger.warning(
                    "Cartridge '%s' not found; proceeding without cartridge", cartridge_id
                )

        # Step 2: Build LLM input from structure
        llm_input = self._input_builder.build(structure, cartridge=cartridge, config=config)

        # Step 3: Build prompt messages
        messages = self._prompt_factory.build_messages(llm_input, cartridge)

        # Step 4: LLM structured output
        try:
            raw_output = self._llm_client.generate(messages)
        except Exception as exc:
            logger.error("LLM generation failed: %s", exc)
            return PaperSkeletonResult.make_fallback(
                structure.document_id, cartridge_id, str(exc)
            )

        # Step 5: Parse raw output
        result = _parse_raw(raw_output, structure.document_id, cartridge_id)

        # Step 6: Validate
        issues = self._validator.validate(result, cartridge)
        errors = [i for i in issues if i.severity == "error"]

        # Step 7: Repair/retry if errors
        if errors:
            result = self._repairer.repair(
                llm_input=llm_input,
                raw_output=raw_output,
                validation_issues=issues,
                cartridge=cartridge,
                llm_client=self._llm_client,
                prompt_factory=self._prompt_factory,
                validator=self._validator,
            )
        else:
            result.validation_issues = issues

        return result
