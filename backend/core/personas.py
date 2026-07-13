"""Episteme Graph — 語り口（ペルソナ）定義モジュール。

レクチャースタジオ・チャットで使用するナレーションおよびレスポンスの語り口設定を提供する。
"""

from __future__ import annotations

from core.course_data import lecture_studio_settings as _lecture_studio_settings

_VALID_PERSONA_IDS = {"", "general_friendly", "general_formal", "expert_friendly", "expert_formal"}

_NARRATION_PROMPTS: dict[str, str] = {
    "general_friendly": (
        "あなたはサイエンス・コミュニケーターとして、一般の学習者に親しみやすく語りかける語り口で"
        "音声読み上げテキストを生成してください。"
        "難しい専門用語は平易な言葉で補足し、聴衆が自然と引き込まれるよう工夫してください。"
    ),
    "general_formal": (
        "あなたは科学ジャーナリストとして、正確で格式のある語り口で"
        "音声読み上げテキストを生成してください。"
        "事実に基づき論理的に説明し、過度な口語表現は避けてください。"
    ),
    "expert_friendly": (
        "あなたは研究室の共同研究者として、専門家同士のフランクな語り口で"
        "音声読み上げテキストを生成してください。"
        "専門用語をそのまま使い、同僚に説明するような自然な口調で話してください。"
    ),
    "expert_formal": (
        "あなたは学会発表・査読者の視点から、厳密かつフォーマルな語り口で"
        "音声読み上げテキストを生成してください。"
        "学術的な正確さを最優先し、引用や根拠を意識した表現を心がけてください。"
    ),
}

_RESPONSE_PROMPTS: dict[str, str] = {
    "general_friendly": (
        "あなたはサイエンス・コミュニケーターとして、一般の学習者に親しみやすく"
        "分かりやすい言葉で回答してください。"
        "具体的な例え話を交えながら、楽しく学べるよう工夫してください。"
    ),
    "general_formal": (
        "あなたは科学ジャーナリストとして、正確かつ格式ある言葉で回答してください。"
        "冗長な表現を避け、要点を明確に伝えてください。"
    ),
    "expert_friendly": (
        "あなたは研究室の共同研究者として、専門的な内容をフランクに回答してください。"
        "専門用語を積極的に使い、深い議論を促してください。"
    ),
    "expert_formal": (
        "あなたは学会発表・査読者の視点から、厳密でフォーマルな言葉で回答してください。"
        "学術的な根拠を意識し、簡潔かつ正確に答えてください。"
    ),
}


def normalize_persona_id(persona_id: str | None) -> str:
    """ペルソナIDを正規化する。未知のIDは空文字列に変換する。"""
    if persona_id is None:
        return ""
    return persona_id if persona_id in _VALID_PERSONA_IDS else ""


def persona_prompt(persona_id: str | None, *, target: str = "narration") -> str:
    """指定ペルソナの語り口指示文を返す。

    Parameters
    ----------
    persona_id : str | None
        ペルソナID。未知または None の場合は空文字列を返す。
    target : str
        "narration" (音声原稿生成用) または "response" (チャット応答用)。

    Returns
    -------
    str
        語り口の指示文。ペルソナIDが未設定または不明な場合は空文字列。
    """
    normalized = normalize_persona_id(persona_id)
    if not normalized:
        return ""

    if target == "response":
        return _RESPONSE_PROMPTS.get(normalized, "")
    return _NARRATION_PROMPTS.get(normalized, "")


def course_persona_settings(course_data: dict) -> dict:
    """コースデータからペルソナ設定を抽出・正規化して返す。

    Parameters
    ----------
    course_data : dict
        コースの JSONB データ。`lecture_studio_settings` キーを含む場合がある。

    Returns
    -------
    dict
        ``{"narration_persona": str, "response_persona": str}``
        未知のペルソナIDは空文字列に正規化される。
    """
    settings = _lecture_studio_settings(course_data)
    return {
        "narration_persona": normalize_persona_id(settings.get("narration_persona")),
        "response_persona": normalize_persona_id(settings.get("response_persona")),
    }
