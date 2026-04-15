"""Episteme Graph — TTS (Text-to-Speech) プロバイダ抽象化レイヤー。

設定に基づいて OpenAI TTS または Google Cloud Text-to-Speech を動的に選択し、
MP3 音声データを返す。

Usage::

    from core.tts import generate_tts_audio

    audio_bytes = generate_tts_audio("読み上げるテキスト")
    if audio_bytes is None:
        # プロバイダが設定されていないか、生成に失敗した
        ...
"""

from __future__ import annotations

import logging

from core.config import get_settings

logger = logging.getLogger(__name__)


def generate_tts_audio(spoken_text: str) -> bytes | None:
    """設定に基づいて TTS プロバイダを動的に選択し、MP3 音声データを返す。

    選択ロジック:
      1. ``llm_api_key`` が ``sk-`` で始まる場合 → OpenAI TTS を使用
      2. ``llm_provider`` が ``google`` または ``gemini-vertex`` の場合
         → Google Cloud Text-to-Speech (ADC 認証) を使用
      3. いずれにも該当しない場合 → エラーログを出力して ``None`` を返す

    Args:
        spoken_text: 音声合成するテキスト。OpenAI は 4096 文字、Google は 5000 文字で切り詰める。

    Returns:
        bytes: MP3 形式の音声データ。生成失敗またはプロバイダ未設定の場合は ``None``。
    """
    settings = get_settings()
    api_key = settings.llm_api_key
    provider = settings.llm_provider

    # --- OpenAI TTS ---
    if api_key.startswith("sk-"):
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
        except Exception:
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
        except Exception:
            logger.exception("Google Cloud TTS の呼び出しに失敗しました")
            return None

    # --- プロバイダ未設定 ---
    logger.error(
        "TTS プロバイダを特定できませんでした。"
        "OpenAI キー (sk-...) を設定するか、LLM_PROVIDER=google を指定してください。"
        "provider=%s api_key_prefix=%s",
        provider,
        (api_key[:8] + "...") if len(api_key) > 8 else "(empty)",
    )
    return None
