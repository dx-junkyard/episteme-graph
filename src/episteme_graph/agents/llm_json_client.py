"""Provider-aware JSON LLM client shared by document analysis agents."""
from __future__ import annotations

import json
import logging
import os
import queue
import threading

logger = logging.getLogger(__name__)


class ProviderJSONLLMClient:
    """Generate JSON through the backend provider router.

    The production backend exposes ``core.llm.generate_text`` as the single
    provider-aware entry point. Keeping agent clients behind this adapter avoids
    bypassing ``LLM_PROVIDER=google`` / Vertex AI authentication.
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        # Keep this explicit. Passing None lets core.llm choose the correct
        # provider-specific analysis model from Settings.
        self._model = model
        self._api_key = api_key
        self.last_raw_text: str | None = None
        self.last_parse_error: str | None = None

    @property
    def model(self) -> str | None:
        return self._model

    def _resolve_max_tokens(self) -> int:
        """Resolve the ``max_tokens`` budget for this call's model tier.

        Precedence: an explicit ``AGENT_LLM_MAX_TOKENS`` override (kept for
        backward compatibility), then the per-tier budget resolved from the
        model name via ``core.llm.max_tokens_for_model`` (fast / standard /
        analysis / deep). Falls back to a conservative default only when the
        backend settings are unavailable (e.g. isolated unit tests).
        """
        override = os.getenv("AGENT_LLM_MAX_TOKENS")
        if override is not None:
            return int(override)
        try:
            from core.llm import max_tokens_for_model

            return max_tokens_for_model(self._model)
        except Exception:
            return 12000

    def generate(self, messages: list[dict], response_schema: dict | None = None) -> dict:
        try:
            from core.llm import generate_text
        except ImportError as exc:
            raise RuntimeError(
                "core.llm.generate_text is required for provider-aware agent LLM calls"
            ) from exc

        try:
            provider = "unknown"
            try:
                from core.config import get_settings
                provider = get_settings().llm_provider
            except Exception:
                pass
            logger.info(
                "Agent LLM JSON generation started provider=%s model=%s messages=%d",
                provider,
                self._model or "<settings-default>",
                len(messages),
            )
            timeout = float(os.getenv("AGENT_LLM_TIMEOUT_SECONDS", "300"))
            wall_timeout = float(
                os.getenv(
                    "AGENT_LLM_WALL_TIMEOUT_SECONDS",
                    str(max(timeout + 30.0, timeout * 1.1)),
                )
            )
            raw_text = _call_with_wall_timeout(
                generate_text,
                wall_timeout,
                messages=messages,
                model=self._model,
                temperature=0.0,
                max_tokens=self._resolve_max_tokens(),
                timeout=timeout,
            )
            self.last_raw_text = raw_text
            self.last_parse_error = None
        except Exception:
            self.last_raw_text = None
            self.last_parse_error = None
            logger.exception(
                "Agent LLM JSON generation failed model=%s messages=%d",
                self._model or "<settings-default>",
                len(messages),
            )
            raise
        return self._parse_json(raw_text or "{}")

    def _parse_json(self, text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                line for line in lines if not line.strip().startswith("```")
            ).strip()
        try:
            parsed = json.loads(text)
            self.last_parse_error = None
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError as exc:
            # A large agent output can hit the model's max-token ceiling and be
            # cut off mid-structure (e.g. inside a string or right after a key).
            # ``json.loads`` then discards everything, including the complete
            # objects already emitted — which collapses the whole stage into a
            # deterministic fallback. Recover the salvageable prefix instead.
            recovered = recover_truncated_json(text)
            if recovered is not None:
                self.last_parse_error = f"recovered_truncated_json: {exc}"
                logger.warning(
                    "Recovered truncated LLM JSON output (likely max_tokens cutoff): "
                    "%s; recovered_keys=%s",
                    exc,
                    sorted(recovered.keys()),
                )
                return recovered
            self.last_parse_error = str(exc)
            logger.warning(
                "Failed to parse LLM JSON output: %s; output_prefix=%r",
                exc,
                text[:500],
            )
            return {}


def _scan_string(s: str, i: int) -> int | None:
    """Return the index just past the JSON string starting at ``s[i] == '"'``.

    Returns ``None`` when the string is unterminated (truncated output).
    """
    j = i + 1
    n = len(s)
    while j < n:
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == '"':
            return j + 1
        j += 1
    return None


_LITERAL_DELIMITERS = set(",}] \t\r\n")


def _scan_literal(s: str, i: int) -> int:
    """Return the index of the first delimiter ending the literal at ``s[i]``.

    Returns ``len(s)`` when the literal runs to the end of the text — i.e. it is
    truncated and therefore not a safe cut point.
    """
    j = i
    n = len(s)
    while j < n and s[j] not in _LITERAL_DELIMITERS:
        j += 1
    return j


def recover_truncated_json(text: str) -> dict | None:
    """Best-effort recovery of a JSON object truncated mid-structure.

    Walks ``text`` with a minimal JSON state machine, remembering the last
    position at which the document could be validly closed (right after a
    completed value or array element). On reaching the truncation point it cuts
    there and appends the missing closing brackets, recovering every complete
    object emitted before the cutoff.

    Returns the parsed ``dict`` (with at least one recovered key), or ``None``
    when nothing salvageable is found or the result is not a JSON object.
    """
    s = text
    n = len(s)
    # Each frame: ["{"|"[", state]. Object states: key|colon|value|comma.
    # Array states: value|comma. The state guards whether a closing quote is a
    # key (not a completed value) or a value (a safe cut point).
    stack: list[list] = []
    safe: tuple[int, str] | None = None

    def closers() -> str:
        return "".join("}" if frame[0] == "{" else "]" for frame in reversed(stack))

    def mark_safe(idx: int) -> None:
        nonlocal safe
        safe = (idx, closers())

    def value_completed(end_idx: int) -> None:
        # The value just ended; its container now expects a comma or close, so
        # cutting here and closing the open frames yields valid JSON.
        if stack:
            stack[-1][1] = "comma"
        mark_safe(end_idx)

    i = 0
    while i < n:
        ch = s[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ch == "{":
            stack.append(["{", "key"])
            i += 1
            continue
        if ch == "[":
            stack.append(["[", "value"])
            i += 1
            continue
        if ch == "}":
            if stack and stack[-1][0] == "{":
                stack.pop()
                value_completed(i + 1)
            i += 1
            continue
        if ch == "]":
            if stack and stack[-1][0] == "[":
                stack.pop()
                value_completed(i + 1)
            i += 1
            continue
        if ch == ",":
            if stack:
                stack[-1][1] = "key" if stack[-1][0] == "{" else "value"
            i += 1
            continue
        if ch == ":":
            if stack and stack[-1][0] == "{":
                stack[-1][1] = "value"
            i += 1
            continue
        if ch == '"':
            end = _scan_string(s, i)
            if end is None:
                break  # truncated inside a string
            if stack and stack[-1][0] == "{" and stack[-1][1] == "key":
                stack[-1][1] = "colon"  # this string is a key, not a value
            else:
                value_completed(end)
            i = end
            continue
        # number / true / false / null
        end = _scan_literal(s, i)
        if end >= n:
            break  # literal truncated at end of text
        value_completed(end)
        i = end

    if safe is None:
        return None
    cut_idx, closing = safe
    candidate = s[:cut_idx] + closing
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) and parsed else None


def _call_with_wall_timeout(func, timeout_seconds: float, *args, **kwargs):
    result_queue: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

    def target() -> None:
        try:
            result_queue.put(("ok", func(*args, **kwargs)))
        except Exception as exc:
            result_queue.put(("error", exc))

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    try:
        status, value = result_queue.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        raise TimeoutError(f"Agent LLM call exceeded {timeout_seconds:.1f}s") from exc
    if status == "error":
        raise value  # type: ignore[misc]
    return value
