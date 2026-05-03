---
name: episteme-graph-ci-tests
description: >
  機能の追加・更新が行われた際に、CIテストパターンの追加・更新を自動的に行うスキル。
  コード変更後に必ず発動し、変更内容に対応するテストが存在するか確認し、
  不足しているテストパターンを生成する。
  「機能を追加して」「エンドポイントを修正して」「ロジックを変更して」
  「スキーマを更新して」などの実装タスク完了後に自動的に発動してください。
---
# Episteme Graph — CI テストパターン更新スキル

## 目的

機能の追加・更新が行われるたびに、対応するテストパターンがCIで実行されるよう
テストコードを追加・更新する。テストの抜け漏れを防ぎ、CI の品質ゲートを維持する。

## 発動タイミング

以下のいずれかに該当する変更が行われた場合、このスキルが発動する:

1. **新しい関数・クラスの追加** — 対応するテストファイルとテストケースを作成
2. **既存関数のシグネチャ変更** — 既存テストの引数・期待値を更新
3. **Pydantic スキーマの変更** (`core/schema.py`) — スキーマのバリデーションテストを更新
4. **API エンドポイントの追加・変更** (`api/routes/`) — エンドポイントのテストを追加・更新
5. **ビジネスロジックの変更** (`core/` 配下) — ロジックのユニットテストを追加・更新
6. **ORM モデルの変更** (`core/models.py`) — モデル関連テストを更新

## テスト更新手順

### Step 1: 変更内容の分析

変更されたファイルを特定し、以下を確認する:

- 追加・変更された関数/メソッド/クラスの一覧
- 変更された引数、戻り値、例外
- 新しい分岐・条件分岐
- 依存関係の変更

### Step 2: 既存テストの確認

`backend/tests/` 配下の既存テストを確認し、以下を判定する:

- 変更対象に対応するテストファイルが存在するか
- 既存テストが変更後のコードと整合するか
- カバーされていないパス（分岐）がないか

### Step 3: テストの追加・更新

以下のルールに従ってテストを作成する:

#### テストファイルの配置規則

| 変更対象 | テストファイル配置先 |
|---|---|
| `backend/core/*.py` | `backend/tests/core/test_<module>.py` |
| `backend/api/routes/*.py` | `backend/tests/api/test_<router>.py` |
| `backend/api/dependencies.py` | `backend/tests/api/test_dependencies.py` |
| `backend/api/main.py` | `backend/tests/api/test_main.py` |
| `src/episteme_graph/agents/<name>/agent.py` | `src/tests/agents/<name>/test_agent.py` |
| `src/episteme_graph/agents/<name>/validator.py` | `src/tests/agents/<name>/test_validator.py` |
| `src/episteme_graph/agents/<name>/schema.py` | `src/tests/agents/<name>/test_schema.py` |
| `src/episteme_graph/agents/<name>/input_builder.py` | `src/tests/agents/<name>/test_input_builder.py` |

#### テストコードの規約

```python
# 1. ファイル先頭に docstring で対象と目的を記述
"""<モジュール名>の単体テスト。

<何をテストするかの簡潔な説明>。
外部 API は一切呼び出さない。
"""

# 2. future annotations を使用
from __future__ import annotations

# 3. テストクラスで論理グループを構成
class TestFunctionName:
    def test_normal_case(self):
        ...
    def test_edge_case(self):
        ...
    def test_error_case(self):
        ...

# 4. 外部依存は mock/patch で分離
from unittest.mock import patch, MagicMock

# 5. パラメータ化テストを活用
@pytest.mark.parametrize("input,expected", [...])
def test_xxx(self, input, expected):
    ...
```

#### テスト設計の原則

1. **外部サービス依存なし**: OpenAI API、PostgreSQL、Neo4j、MinIO への実際の接続は行わない。`unittest.mock.patch` で分離する
2. **conftest.py の設定を活用**: `_override_settings` フィクスチャが自動適用されるため、`get_settings()` は常にテスト用ダミー値を返す
3. **1テスト1検証**: 各テストメソッドは単一の振る舞いを検証する
4. **境界値テスト**: 空リスト、None、空文字列、極端な値のケースを含める
5. **正常系と異常系の両方**: 正常なケースだけでなく、エラーケースや例外発生パターンもテストする
6. **モジュール内インポート**: 外部サービスに依存するモジュールは、テストメソッド内で遅延インポートする（テスト収集時のエラーを防止）

```python
# 外部依存モジュールの遅延インポート例
class TestSomeFunction:
    def test_xxx(self):
        from core.extractor import some_function  # メソッド内でインポート
        result = some_function(...)
        assert result == expected
```

### Step 4: テストの実行確認

テスト追加・更新後、以下のコマンドでテストが通ることを確認する:

```bash
cd backend && python -m pytest tests/ -v
```

特定のテストファイルのみ実行する場合:

```bash
cd backend && python -m pytest tests/core/test_<module>.py -v
```

## テストパターンのチェックリスト

変更タイプごとに最低限カバーすべきテストパターン:

### 新しい関数の追加

- [ ] 正常系: 典型的な入力で期待通りの出力が返る
- [ ] エッジケース: 空入力、None、境界値
- [ ] 異常系: 不正な入力でエラーハンドリングが正しく動作する

### Pydantic スキーマの変更

- [ ] 新しいフィールドのデフォルト値
- [ ] バリデーション: 不正な値でエラーが発生する
- [ ] シリアライズ: `model_dump()` に新フィールドが含まれる
- [ ] 既存テストのヘルパー (`_make_structure` 等) が新フィールドに対応している

### API エンドポイントの追加・変更

- [ ] 正常系: 正しいリクエストで期待通りのレスポンス
- [ ] 認証: 未認証リクエストで 401/403 が返る
- [ ] バリデーション: 不正なリクエストボディで 422 が返る
- [ ] RBAC: 権限不足で 403 が返る

### ビジネスロジックの変更

- [ ] 変更前の動作が維持されるリグレッションテスト
- [ ] 新しいロジックパスのテスト
- [ ] LLM 呼び出しがある場合は mock でレスポンスを固定

### Agent実装のテストパターン（src/episteme_graph/agents/）

Agent追加・変更時は最低限以下をカバーすること:

- [ ] `agent.run()` の正常系: 有効な入力でResultが返る（LLMはmock）
- [ ] `agent.run()` のcartridge=None: cartridgeなしでも動作する
- [ ] `validator.validate()`: 正常出力でissuesが空になる
- [ ] `validator.validate()`: 必須フィールド欠損でissueが出る
- [ ] `schema.py` のシリアライズ: dataclassがJSONシリアライズ可能
- [ ] `input_builder.build()`: 前段agentの出力からLLM入力が構築される
- [ ] repair/retry: validation失敗時にrepairerが呼ばれ再試行が走る

```python
# agents テストのひな型
from unittest.mock import patch, MagicMock

class TestDocumentStructureAgent:
    def test_run_returns_result(self):
        from episteme_graph.agents.document_structure.agent import DocumentStructureAgent
        agent = DocumentStructureAgent()
        with patch.object(agent, '_extract_blocks', return_value=[]):
            result = agent.run("dummy.pdf")
        assert result.document_id is not None

    def test_run_without_cartridge(self):
        from episteme_graph.agents.document_structure.agent import DocumentStructureAgent
        agent = DocumentStructureAgent()
        result = agent.run("dummy.pdf", cartridge_id=None)
        assert result is not None
```

agentsのテスト実行コマンド:
```bash
cd src && python -m pytest tests/ -v
# または特定agent
cd src && python -m pytest tests/agents/document_structure/ -v
```

## CI ワークフローとの連携

テストは `.github/workflows/test.yml` で以下の条件で自動実行される:

- `ura-dev` ブランチへの push / PR
- コマンド: `python -m pytest tests/ -v`
- Python 3.11 環境

テストが CI で失敗しないよう、以下を確認すること:

- `requirements.txt` に記載のないパッケージをインポートしていないか
- 環境変数やファイルパスに依存していないか（`conftest.py` のフィクスチャで対応）
- テスト間の順序依存がないか
