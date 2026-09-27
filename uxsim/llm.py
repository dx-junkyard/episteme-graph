"""ペルソナ側・審判側 LLM（製品の ``core/llm.py`` とは独立 — §7.3）。

- 製品の U層計測に混ざらないよう、製品の LLM モジュールを import しない。呼び出し回数は
  各実装の ``calls`` が自分で数える（PE4 の予算集計に使う）。
- ``ReplayPersonaLLM`` は (system + messages) の sha256 をキーに記録済みの出力を返す（PE5）。
- ``CachingPersonaLLM`` は live の呼び出しを run ディレクトリの ``cache/persona_llm.jsonl`` に記録する。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Protocol, runtime_checkable

from uxsim.schema import now_iso


class ReplayMiss(KeyError):
    """replay cache に該当キーが無い（製品の応答が変わり、画面の投影が分岐した）。"""


class PersonaLLMError(RuntimeError):
    """LLM 呼び出し・JSON 解釈の失敗。"""


@runtime_checkable
class PersonaLLM(Protocol):
    """ペルソナ・審判が使う LLM の最小インターフェース。"""

    calls: int

    def complete_json(self, system: str, messages: list[dict], schema_hint: str) -> dict:
        """system + 会話から JSON オブジェクト 1 つを返す。"""
        ...


def cache_key(system: str, messages: list[dict]) -> str:
    """replay cache のキー（system と messages の決定論的な sha256）。"""
    payload = system + "\n\x1e\n" + json.dumps(messages, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json_object(text: str) -> dict:
    """LLM 出力から JSON オブジェクトを取り出す（コードフェンス・前後の文を許す）。"""
    s = _FENCE_RE.sub("", (text or "").strip())
    try:
        value = json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start < 0 or end <= start:
            raise PersonaLLMError(f"JSON を取り出せません: {s[:200]!r}")
        try:
            value = json.loads(s[start:end + 1])
        except json.JSONDecodeError as exc:
            raise PersonaLLMError(f"JSON を解釈できません: {exc}") from exc
    if not isinstance(value, dict):
        raise PersonaLLMError("JSON オブジェクトではありません")
    return value


def _system_with_schema(system: str, schema_hint: str) -> str:
    if not schema_hint:
        return system
    return f"{system}\n\n# 出力形式\nJSON オブジェクト 1 つだけを出力する。形:\n{schema_hint}"


class OpenAIPersonaLLM:
    """OpenAI chat.completions（``response_format=json_object``）。"""

    def __init__(self, model: str, api_key: str = "", client: Any = None) -> None:
        self.model = model or "gpt-4o-mini"
        self.calls = 0
        if client is None:
            import openai  # 遅延 import

            client = openai.OpenAI(api_key=api_key or None)
        self._client = client

    def complete_json(self, system: str, messages: list[dict], schema_hint: str) -> dict:
        self.calls += 1
        chat = [{"role": "system", "content": _system_with_schema(system, schema_hint)}] + list(messages)
        try:
            resp = self._client.chat.completions.create(
                model=self.model, messages=chat, response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001 — 呼び出し失敗は事実として上げる
            raise PersonaLLMError(f"OpenAI 呼び出し失敗: {exc}") from exc
        return parse_json_object(resp.choices[0].message.content or "")


class AnthropicPersonaLLM:
    """Anthropic Messages API（``anthropic`` は遅延 import）。"""

    def __init__(self, model: str, api_key: str = "", client: Any = None, max_tokens: int = 2048) -> None:
        self.model = model or "claude-sonnet-4-5"
        self.max_tokens = max_tokens
        self.calls = 0
        if client is None:
            import anthropic  # 遅延 import（uxsim 専用の依存）

            client = anthropic.Anthropic(api_key=api_key or None)
        self._client = client

    def complete_json(self, system: str, messages: list[dict], schema_hint: str) -> dict:
        self.calls += 1
        try:
            resp = self._client.messages.create(
                model=self.model, max_tokens=self.max_tokens,
                system=_system_with_schema(system, schema_hint), messages=list(messages),
            )
        except Exception as exc:  # noqa: BLE001
            raise PersonaLLMError(f"Anthropic 呼び出し失敗: {exc}") from exc
        text = "".join(getattr(b, "text", "") for b in getattr(resp, "content", []) or [])
        return parse_json_object(text)


class ReplayPersonaLLM:
    """記録済み cache だけで答える。無いキーは ``ReplayMiss``。"""

    def __init__(self, cache: dict[str, dict]) -> None:
        self.cache = dict(cache)
        self.calls = 0
        self.hits = 0

    @classmethod
    def from_jsonl(cls, path: Path) -> "ReplayPersonaLLM":
        return cls(load_cache_jsonl(path))

    def complete_json(self, system: str, messages: list[dict], schema_hint: str) -> dict:
        key = cache_key(system, messages)
        if key not in self.cache:
            raise ReplayMiss(key)
        self.hits += 1
        return dict(self.cache[key])


class ScriptedPersonaLLM:
    """テスト・乾式実行用。出力の列か関数から順に返す（外部 API を呼ばない）。"""

    def __init__(self, outputs: Iterable[dict] | Callable[[str, list[dict]], dict]) -> None:
        self._fn = outputs if callable(outputs) else None
        self._items = [] if callable(outputs) else list(outputs)
        self.calls = 0

    def complete_json(self, system: str, messages: list[dict], schema_hint: str) -> dict:
        self.calls += 1
        if self._fn is not None:
            return dict(self._fn(system, messages))
        if not self._items:
            raise PersonaLLMError("scripted outputs が尽きました")
        return dict(self._items.pop(0))


def load_cache_jsonl(path: Path) -> dict[str, dict]:
    """``persona_llm.jsonl``（1 行 = {key, output, at}）を dict に読む。壊れた行は飛ばす。"""
    cache: dict[str, dict] = {}
    if not path.is_file():
        return cache
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and "key" in row and isinstance(row.get("output"), dict):
            cache[row["key"]] = row["output"]
    return cache


class CachingPersonaLLM:
    """内側 LLM の全出力を jsonl に記録する wrapper（PE5）。"""

    def __init__(self, inner: PersonaLLM, path: Path) -> None:
        self.inner = inner
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def calls(self) -> int:
        return self.inner.calls

    def complete_json(self, system: str, messages: list[dict], schema_hint: str) -> dict:
        output = self.inner.complete_json(system, messages, schema_hint)
        row = {"key": cache_key(system, messages), "output": output, "at": now_iso()}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return output


class ReplayThenLivePersonaLLM:
    """replay を先に引き、miss したら live に切り替える（§7.3 — 分岐点を記録する）。

    ``live`` が None なら miss をそのまま上げる。``diverged_at_call`` に最初の miss の
    呼び出し順を残す。``calls`` は live 側の実呼び出し回数（予算の対象）。
    """

    def __init__(self, replay: ReplayPersonaLLM, live: Optional[PersonaLLM] = None) -> None:
        self.replay = replay
        self.live = live
        self.diverged = False
        self.diverged_at_call: Optional[int] = None
        self._n = 0

    @property
    def calls(self) -> int:
        return self.live.calls if (self.live is not None and self.diverged) else 0

    def complete_json(self, system: str, messages: list[dict], schema_hint: str) -> dict:
        self._n += 1
        if not self.diverged:
            try:
                return self.replay.complete_json(system, messages, schema_hint)
            except ReplayMiss:
                if self.live is None:
                    raise
                self.diverged = True
                self.diverged_at_call = self._n
        assert self.live is not None
        return self.live.complete_json(system, messages, schema_hint)


class ScriptedFollowerLLM:
    """LLM を呼ばずに台本どおり進む（費用ゼロの配線確認用）。

    ``PersonaAgent`` は ``scripted`` 属性を見て LLM を呼ばず、各ステップの最初に許された行為を
    提案引数のまま実行する。think-aloud は固定文（発見の材料は審判 A/B/E の決定論検査だけになる）。
    """

    scripted = True

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, system: str, messages: list[dict], schema_hint: str) -> dict:  # pragma: no cover
        raise PersonaLLMError("scripted プロバイダは complete_json を呼ばない")


class MailboxPersonaLLM:
    """Claude Code の子エージェントが応える mailbox 経由のペルソナ LLM（外部 API を呼ばない）。"""

    def __init__(self, root: Optional[Path] = None) -> None:
        from uxsim.mailbox import Mailbox, default_root

        self.box = Mailbox(root or default_root())
        self.calls = 0

    def complete_json(self, system: str, messages: list[dict], schema_hint: str) -> dict:
        self.calls += 1
        data = self.box.ask("persona", {"system": system, "messages": messages, "schema_hint": schema_hint})
        out = data.get("output")
        if isinstance(out, str):
            out = parse_json_object(out)
        if not isinstance(out, dict):
            raise PersonaLLMError("mailbox 応答に output（JSON オブジェクト）が無い")
        return out


class ClaudeCliPersonaLLM:
    """`claude -p` を 1 ステップ 1 回呼ぶ（CLI のログインが要る。この機では未ログイン）。"""

    def __init__(self, model: str = "", exe: str = "") -> None:
        import os

        self.model = model or "sonnet"
        self.exe = exe or os.environ.get("CLAUDE_CODE_EXECPATH") or "claude"
        self.calls = 0

    def complete_json(self, system: str, messages: list[dict], schema_hint: str) -> dict:
        import os
        import subprocess
        import tempfile

        self.calls += 1
        prompt = "\n\n".join(f"[{m.get('role')}]\n{m.get('content')}" for m in messages)
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
        with tempfile.TemporaryDirectory() as cwd:
            proc = subprocess.run(
                [self.exe, "-p", prompt, "--output-format", "json", "--model", self.model, "--max-turns", "1",
                 "--tools", "", "--no-session-persistence", "--system-prompt", _system_with_schema(system, schema_hint)],
                capture_output=True, text=True, cwd=cwd, env=env, timeout=600,
            )
        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise PersonaLLMError(f"claude -p の出力を読めない: {proc.stdout[:200]!r}") from exc
        if data.get("is_error"):
            raise PersonaLLMError(f"claude -p 失敗: {data.get('result')}")
        return parse_json_object(str(data.get("result") or ""))


def make_live_llm(provider: str, model: str, api_key: str) -> PersonaLLM:
    """プロバイダ名から live 実装を作る（``replay`` は live ではないので ValueError）。"""
    if provider == "scripted":
        return ScriptedFollowerLLM()
    if provider == "mailbox":
        return MailboxPersonaLLM()
    if provider == "claude_cli":
        return ClaudeCliPersonaLLM(model)
    if provider == "openai":
        return OpenAIPersonaLLM(model, api_key)
    if provider == "anthropic":
        return AnthropicPersonaLLM(model, api_key)
    raise ValueError(f"live のプロバイダではありません: {provider!r}")
