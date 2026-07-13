"""Episteme Graph — TTS (Text-to-Speech) プロバイダ抽象化レイヤー。

設定に基づいて OpenAI TTS または Google Cloud Text-to-Speech を動的に選択し、
MP3 音声データを返す。

Usage::

    from core.tts import generate_tts_audio, TtsFatalError

    try:
        audio_bytes = generate_tts_audio("読み上げるテキスト", language="ja")
    except TtsFatalError:
        # 設定起因の恒久エラー。リトライ不要
        ...
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from core.config import get_settings
from core.llm_usage.observe import observe_tts

logger = logging.getLogger(__name__)


def _safe_len(value: Any) -> int:
    """``len()`` が例外を投げうる想定外入力でも安全に 0 を返す（観測フック専用）。"""
    try:
        return len(value)
    except Exception:
        return 0


# 音声読み上げ前のテキスト整形: LaTeX・markdown 記号・システムマーカーを除去する。
_SPEECH_STRIP_PATTERNS = (
    re.compile(r"\$\$.*?\$\$", re.DOTALL),          # ディスプレイ数式
    re.compile(r"\$[^$\n]+\$"),                      # インライン数式
    re.compile(r"\[ACTION_BUTTON:[^\]]*\]"),         # アクションボタン記法
    re.compile(r"\[[^\]]*について詳しく聞く\]"),      # ドリルダウンマーカー
    re.compile(r"\[出典\d+\]"),                      # 出典マーカー
    re.compile(r"[*_#`>|]+"),                        # markdown 記号
)


def strip_text_for_speech(text: str, limit: int = 4000) -> str:
    """回答テキストを TTS 向けに整形する（記号除去・空白正規化・長さ上限）。

    ハンズフリー音声会話（カジュアル対話モード）の読み上げ前処理。
    """
    cleaned = text or ""
    for pat in _SPEECH_STRIP_PATTERNS:
        cleaned = pat.sub(" ", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{2,}", "\n", cleaned).strip()
    return cleaned[:limit]


class TtsFatalError(Exception):
    """TTS プロバイダの設定に起因する恒久的なエラー（リトライ不要）。

    API キー未設定、API 未有効化、権限不足など、何度リトライしても
    成功しないことが確実なケースで送出される。
    """


def generate_tts_audio(spoken_text: str, language: str = "ja") -> bytes | None:
    """設定に基づいて TTS プロバイダを動的に選択し、MP3 音声データを返す。

    選択ロジック:
      1. ``llm_provider`` が ``openai`` の場合 → OpenAI TTS を使用
      2. ``llm_provider`` が ``google`` または ``gemini-vertex`` の場合
         → Google Cloud Text-to-Speech (ADC 認証) を使用
      3. いずれにも該当しない場合 → エラーログを出力して ``None`` を返す

    Args:
        spoken_text: 音声合成するテキスト。OpenAI は 4096 文字、Google は 5000 文字で切り詰める。
        language: 読み上げ言語 (``"ja"`` / ``"en"``)。既定は ``"ja"``（後方互換）。
            OpenAI は多言語対応の voice を使うため voice 自体の切替は不要（テキストに従う）。
            Google Cloud TTS はこの値で ``language_code`` を切り替える
            （migration 040: ``ja-JP`` ハードコード撤廃）。

    Returns:
        bytes: MP3 形式の音声データ。生成失敗またはプロバイダ未設定の場合は ``None``。

    Raises:
        TtsFatalError: API 未有効化・認証エラーなど、リトライしても回復しない恒久的なエラー。
    """
    settings = get_settings()
    api_key = settings.llm_api_key
    provider = settings.llm_provider
    # lecture_tts_voice 未設定の Settings 互換オブジェクト（テストのモック等）でも
    # 動作するよう getattr で防御的に取得する。
    voice = getattr(settings, "lecture_tts_voice", None) or "alloy"
    google_language_code = "en-US" if language == "en" else "ja-JP"

    # --- OpenAI TTS ---
    if provider == "openai":
        text_to_speak = spoken_text[:4096]
        synthesis_attempted = False
        started: float | None = None
        try:
            import openai  # type: ignore[import]
            client = openai.OpenAI(api_key=api_key)
            synthesis_attempted = True
            started = time.monotonic()
            response = client.audio.speech.create(
                model="tts-1",
                voice=voice,
                input=text_to_speak,
                response_format="mp3",
            )
        except Exception as exc:
            if synthesis_attempted:
                # 実際に合成を試行した場合のみ記録する（characters は実際に送ったテキスト長）。
                observe_tts(
                    provider="openai",
                    model="tts-1",
                    characters=_safe_len(text_to_speak),
                    error=exc,
                    started_monotonic=started,
                )
            exc_str = str(exc)
            # 認証エラーはリトライしても回復しない
            if "401" in exc_str or "AuthenticationError" in type(exc).__name__:
                raise TtsFatalError(
                    f"OpenAI TTS 認証エラー: API キーを確認してください。({exc_str})"
                ) from exc
            logger.exception("OpenAI TTS の呼び出しに失敗しました")
            return None
        observe_tts(
            provider="openai",
            model="tts-1",
            characters=_safe_len(text_to_speak),
            started_monotonic=started,
        )
        return response.content

    # --- Google Cloud Text-to-Speech ---
    if provider in ("google", "gemini-vertex"):
        text_to_speak = spoken_text[:5000]
        synthesis_attempted = False
        started: float | None = None
        tts_model_label = f"google-tts:{google_language_code}"
        try:
            from google.cloud import texttospeech  # type: ignore[import]
            tts_client = texttospeech.TextToSpeechClient()
            synthesis_input = texttospeech.SynthesisInput(text=text_to_speak)
            voice_params = texttospeech.VoiceSelectionParams(
                language_code=google_language_code,
                ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL,
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
            )
            synthesis_attempted = True
            started = time.monotonic()
            tts_response = tts_client.synthesize_speech(
                input=synthesis_input,
                voice=voice_params,
                audio_config=audio_config,
            )
        except Exception as exc:
            if synthesis_attempted:
                observe_tts(
                    provider=provider,
                    model=tts_model_label,
                    characters=_safe_len(text_to_speak),
                    error=exc,
                    started_monotonic=started,
                )
            exc_str = str(exc)
            # SERVICE_DISABLED はリトライしても回復しない恒久エラー
            if "SERVICE_DISABLED" in exc_str or "has not been used" in exc_str:
                raise TtsFatalError(
                    "Cloud Text-to-Speech API が GCP プロジェクトで無効です。"
                    "以下の URL から有効化してください: "
                    "https://console.developers.google.com/apis/api/texttospeech.googleapis.com/overview"
                ) from exc
            logger.exception("Google Cloud TTS の呼び出しに失敗しました")
            return None
        observe_tts(
            provider=provider,
            model=tts_model_label,
            characters=_safe_len(text_to_speak),
            started_monotonic=started,
        )
        return tts_response.audio_content

    # --- プロバイダ未設定 ---
    logger.error(
        "TTS プロバイダを特定できませんでした。"
        "LLM_PROVIDER=openai または LLM_PROVIDER=google を指定してください。"
        "provider=%s",
        provider,
    )
    return None
