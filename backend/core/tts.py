"""Episteme Graph — TTS (Text-to-Speech) プロバイダ抽象化レイヤー。

設定に基づいて OpenAI TTS または Google Cloud Text-to-Speech を動的に選択し、
MP3 音声データを返す。

Usage::

    from core.tts import generate_tts_audio, TtsFatalError

    try:
        audio_bytes = generate_tts_audio("読み上げるテキスト")
    except TtsFatalError:
        # 設定起因の恒久エラー。リトライ不要
        ...
"""

from __future__ import annotations

import logging

from core.config import get_settings

logger = logging.getLogger(__name__)


class TtsFatalError(Exception):
    """TTS プロバイダの設定に起因する恒久的なエラー（リトライ不要）。

    API キー未設定、API 未有効化、権限不足など、何度リトライしても
    成功しないことが確実なケースで送出される。
    """


def generate_tts_audio(spoken_text: str) -> bytes | None:
    """設定に基づいて TTS プロバイダを動的に選択し、MP3 音声データを返す。

    選択ロジック:
      1. ``llm_provider`` が ``openai`` の場合 → OpenAI TTS を使用
      2. ``llm_provider`` が ``google`` または ``gemini-vertex`` の場合
         → Google Cloud Text-to-Speech (ADC 認証) を使用
      3. いずれにも該当しない場合 → エラーログを出力して ``None`` を返す

    Args:
        spoken_text: 音声合成するテキスト。OpenAI は 4096 文字、Google は 5000 文字で切り詰める。

    Returns:
        bytes: MP3 形式の音声データ。生成失敗またはプロバイダ未設定の場合は ``None``。

    Raises:
        TtsFatalError: API 未有効化・認証エラーなど、リトライしても回復しない恒久的なエラー。
    """
    settings = get_settings()
    api_key = settings.llm_api_key
    provider = settings.llm_provider

    # --- OpenAI TTS ---
    if provider == "openai":
        try:
            import openai  # type: ignore[import]
            client = openai.OpenAI(api_key=api_key)
            response = client.audio.speech.create(
                model="tts-1",
                voice="alloy",
                input=spoken_text[:4096],
                response_format="mp3",
            )
            return response.content
        except Exception as exc:
            exc_str = str(exc)
            # 認証エラーはリトライしても回復しない
            if "401" in exc_str or "AuthenticationError" in type(exc).__name__:
                raise TtsFatalError(
                    f"OpenAI TTS 認証エラー: API キーを確認してください。({exc_str})"
                ) from exc
            logger.exception("OpenAI TTS の呼び出しに失敗しました")
            return None

    # --- Google Cloud Text-to-Speech ---
    if provider in ("google", "gemini-vertex"):
        try:
            from google.cloud import texttospeech  # type: ignore[import]
            tts_client = texttospeech.TextToSpeechClient()
            synthesis_input = texttospeech.SynthesisInput(text=spoken_text[:5000])
            voice_params = texttospeech.VoiceSelectionParams(
                language_code="ja-JP",
                ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL,
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
            )
            tts_response = tts_client.synthesize_speech(
                input=synthesis_input,
                voice=voice_params,
                audio_config=audio_config,
            )
            return tts_response.audio_content
        except Exception as exc:
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

    # --- プロバイダ未設定 ---
    logger.error(
        "TTS プロバイダを特定できませんでした。"
        "LLM_PROVIDER=openai または LLM_PROVIDER=google を指定してください。"
        "provider=%s",
        provider,
    )
    return None
