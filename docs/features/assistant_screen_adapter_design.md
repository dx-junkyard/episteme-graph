# 画面文脈アダプター（Assistant Screen Adapter — AI 対話に「いま見ている画面」を渡す層）

> **状態: 実装済み（正本・凍結）**（Phase 1 = グラフレビュー。Phase 2〜4 は §6 予約）
> （2026-09-06 起票・同日実装。migration なし・新テーブルなし・LLM 呼び出し回数不変の
> 読み時解決。実装記録は §10）

**正本**: 本ドキュメント。
**関連**: [グラフの論文層](graph_paper_layer_design.md)（PL1〜PL8 — Phase 1 の第1適用先で
解決器が読む DTO の正本）/ [グラフ対話レビュー](graph_dialogue_review_design.md)（GR1〜GR8 —
第1適用先の画面と対話経路）/ [要素検討ワークスペース](element_deliberation_workspace_design.md)
（W1〜W9 — ノード対話の実体である W層セッション）/
[Admin Copilot](admin_assistant_design.md)（P1〜P8 — `screen_context` の先行実装。本層は
その参照形を全画面へ一般化する）/ [チャット型 AI の共通規約](assistant_common_infra_design.md)
（window_history / CostGate / degraded の規約を本層も継承する）。

---

## 1. 目的 — 「画面で見ているもの」と「AI が知っているもの」を一致させる

各画面には AI 対話（テキスト・音声）が置かれているが、**AI に渡っているのは対話対象の
ID から導出した固定の grounding だけ**で、教員・学習者がいま画面で見ている選択・表示
モード・付随情報は届いていない。グラフレビューでは、右ペインに論文層（章・式番号・逐語
引用・図表）が描かれているのに、ノード対話・グラフ全体対話の LLM はそれを一切知らない
（2026-09-06 調査: `dialogue.grounding_to_text` は component の名前・要約・60字ラベル抜粋
のみ、`graph_grounding_to_text` は主グラフ骨格と件数のみ、フロントの送信ボディは
`{content}` のみ）。結果として「グラフを見ながら AI と論文の詳細を確かめる」ことができない。

本層は、**画面が参照だけを渡し、サーバが既存の権限ゲート付き core でそれを解決して
grounding に足す**アダプターを規約化する。LLM の呼び出し回数・CostGate・確定の主体
（人間）は変えない。

## 2. 不変条項（SA1〜SA7）

| # | 条項 | 根拠・継承元 |
|---|---|---|
| SA1 | **画面は参照だけを渡す**。選択要素の種別と ID・document_id・表示モード・見えている項目の ID と短い題名（40字以内）まで。描画されたテキスト・数値・DTO 本体を送らない | クライアント申告を根拠と区別できなくなる。W層 `selected_context` の既存規約（「範囲の手がかりであって根拠ではない」）を全画面に拡張 |
| SA2 | **解決はサーバ側・既存の権限ゲート付き core 経由**。権限外・不在の参照はエラーにせず静かに落とし、落とした事実は事実文にしない（教員に「見えないものがある」と言わせない） | Copilot P1 / W5 / GR6 / CR1 の fail-closed |
| SA3 | **決定論・非LLM・事前解決**。LLM にデータ取得ツールを持たせない。送信前にアダプターが解決し、1ターン1コール（GR5 / W6 / Copilot P6）と各 CostGate を不変に保つ | assistant_common_infra_design §CostGate |
| SA4 | **数値・内部 ID 非表示**。confidence / weight / score / 件数を事実文に載せない。式は印字番号・図表は figure_label・章は見出し | PL4 / PL7 / W8 / vision 改訂原則4 |
| SA5 | **読み取り専用**。アダプターは書き込み API を呼ばず、解決結果から承認・却下・保存の経路を作らない（音声経路も同じ） | GR1「音声から承認 API を呼ぶ経路は作らない」 |
| SA6 | **画面状態を保存しない**。`screen_context` はメッセージ行・セッション行・監査に永続化しない（LLM へ渡す当該ターンの入力にだけ使う） | W層 `selected_context` の「message に永続化しない」規約 / 観察面を広げない（UC4 / PN-1） |
| SA7 | **プロンプト予算はコード定数**。解決器ごとに項目上限・文字上限を持ち、env で緩めない。解決結果は固定ヘッダ付きの独立ブロックで、利用者の発話・既存 grounding と混ぜない | graph_dialogue の `_MAX_*` 定数と同じ扱い |

## 3. 2層構造

```
[画面]  window.<Screen>.getScreenContext()  →  参照だけの ScreenContext（SA1）
   │  送信ボディの optional フィールド screen_context
   ▼
[route] 権限確認済みの対象（document / course）に対し
        core.assistant_context.resolve(ctx, sources) を呼ぶ   ← sources は route が
   │    既存の権限ゲート付き経路で組む（論文層 DTO 等）          既存関数で取得（SA2）
   ▼
[core]  registry: (screen, kind) → resolver(ctx, sources) -> facts[]   純関数・非LLM（SA3）
        render_block(facts) → "[画面文脈] …" の独立ブロック（SA4 / SA7）
   │
   ▼
[route] 当該ターンの user content の先頭に prepend（保存する content には含めない = SA6）
```

- **なぜ grounding_text ではなく当該ターンの user content か**: grounding は対話対象
  （component / document_graph）に対して安定だが、画面文脈はターンごとに変わる
  （グラフ全体対話中に選択ノードを移す等）。W層 `selected_context` と同じ位置に置くことで
  「保存しない・当該ターンだけ」が構造的に守られる。
- **なぜ core は DTO を受け取り DB を読まないか**: `core/deliberation` / `core/graph_paper_layer`
  と同じ純関数規律（FastAPI / sqlalchemy 非 import）。権限判定と取得は route の責務。

## 4. 契約

### 4.1 画面側（全画面共通の形）

```jsonc
{
  "screen": "graph_review",                 // 画面 ID（登録済み語彙のみ。未知は無視）
  "selection": {                            // 参照だけ。キーは画面ごとに宣言
    "document_id": "…", "node_id": "…", "component_id": "…" | null, "graph_layer": "main"
  },
  "view": { "mode": "graph" | "paper", "layer": "main" | "detail" | "all" },
  "visible_entities": [ { "type": "node", "id": "…", "title": "≤40字" } ]   // 最大20件
}
```

- 既存の Copilot `collectScreenContext()`（`tab` / `selection` / `visible_entities`）と
  原稿スタジオ `LectureStudio.getScreenContext()` はこの形の先行実装。Phase 3 で `screen`
  キーを足して同じ語彙に揃える（`tab` は互換で残す）。
- `title` は40字以内・本文の抜粋を入れない。`visible_entities` は「見えている」の事実で
  あって内容ではない。

### 4.2 リクエスト（Phase 1: W層 messages / graph-sessions messages）

`MessageCreateRequest.screen_context: ScreenContextPayload | None`（optional・未指定は
従来動作）。Pydantic は `extra="forbid"`・`id`/`document_id` ≤160字・`title` ≤40字・
`visible_entities` ≤20件・`screen` は登録語彙のみ（未知は 422 ではなく **無視** — 画面の
提供が壊れても対話を止めない）。

### 4.3 core（`backend/core/assistant_context/`）

```python
# schema.py
SCREEN_GRAPH_REVIEW = "graph_review"
KNOWN_SCREENS: tuple[str, ...]
@dataclass(frozen=True) class ScreenContext: screen / selection: dict / view: dict / visible_entities: list[dict]
def normalize_screen_context(raw: Any) -> ScreenContext | None   # 上限を切る・未知 screen は None
BLOCK_HEADER = "[画面文脈 — 教員がいま画面で選んでいる対象を、サーバが解析結果から解決した事実。根拠ではなく範囲の手がかり]"
MAX_BLOCK_CHARS = 2400                                            # SA7

# registry.py
Resolver = Callable[[ScreenContext, Mapping[str, Any]], list[str]]
def register(screen: str, kind: str, resolver: Resolver) -> None
def resolve(ctx: ScreenContext, sources: Mapping[str, Any]) -> list[str]   # 該当 resolver を順に適用
def render_block(facts: list[str]) -> str                                  # 空なら ""

# resolvers/graph_review.py（sources = {"paper_layer": <論文層 DTO>}）
kind "graph_node"     : selection.node_id が DTO.nodes に在れば、位置（章）/ 式（≤5, display_label + plain_text ≤120字）/
                        逐語引用（≤3）/ 図表（≤3）/ 記号（≤5）/ 導出 step（≤3）/ 中心命題での役割 / contextual 説明
                        （status ラベル付き）/ narrative_role。unlocated は事実文1行。
kind "document_graph" : DTO.paper.sections の見出し順に「章 → 掛かるノードのラベル」（≤30行）+ 被覆
                        （掛かっていない章・式・図のラベル列挙、各 ≤5 — 件数は書かない）。
kind "view"           : 表示モードの事実1行（「論文の順」を見ている / 式の詳細層を表示中）。
```

- `FORBIDDEN_KEYS`（`graph_paper_layer.schema`）を再利用し、事実文に数値キーの値を書かない。
- resolver は入力を mutate しない・例外を外へ出さない（1 resolver の失敗はその facts だけ欠ける）。

### 4.4 route（`routes/deliberation.py`）

- 要素対話 `POST .../sessions/{sid}/messages` と グラフ全体対話 `POST .../graph-sessions/{sid}/messages`
  の双方で、`body.screen_context` があり `selection.document_id` がセッションの document と
  一致するときだけ解決する（不一致は無視 = 他文書の参照で越境させない）。
- sources の組み立ては `routes/theory_components.py` の既存ヘルパ（`_components_for_document` /
  `_normalize_stored_component_graph` / `_build_component_graph_payload` / `document_run_artifacts` /
  `_paper_layer_figure_rows` / `_paper_layer_explanation_rows` / `_build_paper_layer_payload`）を
  1つの関数 `build_paper_layer_for_document(document_id)` に束ねて再利用する（GET paper-layer
  と同一経路 — 二重実装しない）。失敗は空 sources（SA2 fail-soft）。
- 解決結果ブロックは `llm_user_content` の先頭に prepend（`selected_context` と同じ「当該ターンのみ・
  非保存」の位置。順序は screen_context → selected_context → 発話 — 固定ヘッダ付きブロックを
  発話と混ぜない SA7 のため先頭に置く。§10.3）。**保存する message.content は不変**（SA6）。
- CostGate 消費位置は不変（全 422 経路の後）。

## 5. Phase 1 — グラフレビュー（第1適用先）

### 5.1 対話の論文対応（本層の本体）
- フロント `admin-graph-review.js`: `window.GraphReview.getScreenContext()` を公開し、
  ノード対話・グラフ全体対話の送信ボディに `screen_context` を足す（音声経路は
  `sendChatText` に合流しているため自動的に同じボディになる）。
- バックエンド: §4.3 / §4.4。

### 5.2 論文層の穴の是正（同時に行う — アダプターが空を渡さないため）
- `_normalize_stored_component_graph` が `agent_component_id` を落としている（`NODE_COMPONENT_REF_KEYS`
  の先頭キー）→ additive に通す。
- `theory_component_graphs` 行が無く `_build_component_graph_payload` にフォールバックした場合、
  論文層は `available: true` のまま全ノード空になる → `facts[]` に専用の事実文
  「理論操作グラフが未構築のため、論文との対応は導出できません（再解析で構築されます）」を足し、
  UI はその事実文を出す。
- テスト: 本流パイプラインの永続化形（`persist_component_graph` が保存する node 形 = agent claim ID・
  `agent_component_id` 付き）に沿った現実的 fixture を `test_graph_paper_layer_core.py` に追加。

### 5.3 表示の是正（`graph_paper_layer_design.md` §11 に記録）
- キャンバスのノードに「論文要素あり」の離散マーク（式・引用・図の**種別**のみ。件数は出さない = PL4）。
- 詳細ペイン: `claims[]` を描く（DTO にあるが未描画）／ノード未選択時に「ノードを選ぶと論文側の
  対応と対話が使えます」の案内文。
- 「論文の順」ビュー ⇄ グラフの双方向ハイライト（グラフ表示中でも選択ノードの章チップが分かる）。
- アンカー・マニュアル: 新しい操作要素を足す場合のみ3点セット（マークは操作要素ではない）。

## 6. Phase 2〜4（本書で予約・着手時に §を足す）

| Phase | 画面 | 参照 | sources | 備考 |
|---|---|---|---|---|
| 2 | W層 要素モーダル（`deliberation.js`） | 中心要素・レンズ focus・表示中レーン | context_lens（既存 grounding の重複を避け、**表示モードの事実**のみ足す） | 既存 `selected_context` を `screen_context.selection` の一部へ吸収（互換維持） |
| 3 | Admin Copilot（全タブ） | 既存 `collectScreenContext()` に `screen` を追加 | 教材タブ: projector の status 事実 / コース管理: course_data の事実 | capability registry のロールで fail-closed（P1） |
| 4 | 学習チャット（`app.js`） | 表示中トピック・スライド・選択テキスト（既存フィールド） | `learner_context_common` の学習者射影のみ | 本人可視の範囲だけ。数値・内部 ID・生 TeX 遮断を継承 |

## 7. 非スコープ（v1）
- LLM のツール呼び出し（agentic データ取得）— SA3 で恒久排除。
- 画面状態の保存・履歴化・教員向け集約 — SA6。
- 解決結果からの書き込み（承認・保存）— SA5。
- 画面のスクリーンショット・DOM テキストの送信 — SA1。
- 学習者向け Phase 4 の実装（設計予約のみ）。

## 8. ガードレール（`backend/tests/test_assistant_context_{core,guardrails}.py` + 経路側）
- `core/assistant_context/` が fastapi / sqlalchemy / `core.llm` を import しない。
- resolver が入力を mutate しない・数値キー（`FORBIDDEN_KEYS`）の値を出力に含めない・
  内部 ID（`eq_op_` / `theory_op_` / `ev_` / `claim_`）を事実文に出さない。
- `render_block` の出力が `MAX_BLOCK_CHARS` を超えない・空 facts は空文字。
- 未知 `screen` / 上限超過 / 型不正は例外にせず None または切り詰め。
- route: `screen_context` を保存 message に含めない（保存 content と LLM 入力を別に検査）・
  document 不一致の参照を無視する・CostGate 消費位置が不変・`screen_context` 無しの
  リクエストの LLM 入力が従来と同一。
- フロント: 送信ボディに `screen_context` を含む・`getScreenContext` が本文（display_text 等）
  を返さない（静的 grep）・音声経路も `sendChatText` 経由。

## 9. 想定される反論と応答
- **「画面のテキストをそのまま渡す方が簡単で正確」** — 渡した瞬間に、AI の回答がクライアント
  申告に基づくのか出典に基づくのか区別できなくなる。既存の `selected_context` が「根拠ではない」
  と明示して渡しているのはこのため。参照→サーバ解決なら出典側の権限・数値非表示がそのまま効く。
- **「LLM に取得ツールを持たせれば柔軟」** — 1ターン複数コールになり、CostGate と U層計測の
  前提（1コール=1ターン）が崩れる。まず決定論の事前解決で足りるかを実測してから判断する。

## 10. 実装記録

**Phase 1（2026-09-06・グラフレビュー）**: migration なし・新テーブルなし・新エンドポイント
なし・LLM 呼び出し回数不変（1ターン1コール・CostGate 消費位置も不変）。

### 10.1 ファイル

| 層 | ファイル | 内容 |
|---|---|---|
| core | `backend/core/assistant_context/{__init__,schema,registry}.py` + `resolvers/graph_review.py` | 語彙・上限・`normalize_screen_context` / レジストリと `resolve` / `render_block` / グラフレビューの3解決器（`graph_node` / `document_graph` / `view`）。FastAPI・sqlalchemy・LLM 非 import |
| route | `backend/api/routes/deliberation.py` | `ScreenContextPayload` + 両リクエストモデルの `screen_context` + 共通ヘルパ `_screen_context_block()` + 両 messages 経路の配線 |
| route | `backend/api/routes/theory_components.py` | `build_paper_layer_for_document()` の抽出（GET paper-layer の本体）+ `_normalize_stored_component_graph` の `agent_component_id` 通し + `_build_paper_layer_payload(..., extra_facts=)` |
| core | `backend/core/graph_paper_layer/{schema,builder}.py` | `FACT_NO_STORED_GRAPH` の追加と `build_paper_layer(..., extra_facts=None)`（additive） |
| front | `frontend/public/js/admin-graph-review.js` | `getScreenContext()` の公開と送信ボディへの同梱（`graph_paper_layer_design.md` §11.4 に記録） |

### 10.2 エンドポイント（新設ゼロ・既存2本に optional フィールド）

- `POST /api/admin/deliberation/sessions/{sid}/messages`（要素対話）
- `POST /api/admin/deliberation/documents/{doc}/graph-sessions/{sid}/messages`（グラフ全体対話）

いずれも `screen_context: ScreenContextPayload | None`（未指定は従来動作でバイト等価）。
`extra="forbid"` だが**未知の `screen`・上限超過では 422 にしない**（§4.2）: `screen` は40字で、
`visible_entities` は20件・`id` 160字・`title` 40字で**切り詰める**バリデータを持ち、拒否しない
（画面の提供が壊れても対話は止めない）。上限の値は route で再定義せず
`core.assistant_context.schema` の定数を import する（SA7）。

### 10.3 prepend の順序（設計 §4.4 からの意図的な差分）

実装の順序は **画面文脈ブロック → `selected_context` の前置き → 発話** で、設計 §4.4 の
記述（selected_context → screen_context → 発話）と前2つが逆になっている。画面文脈は
固定ヘッダ付きの**独立ブロック**（SA7）であり、入力の先頭に置いたほうが「利用者の発話と
混ざらない」という条項の意図に沿うため、こちらを正とした（ガードレールは
`llm_user_content.startswith(BLOCK_HEADER)` を固定している）。発話が末尾である点、保存
content が生の発話である点（SA6）は設計どおり。

### 10.4 共通ヘルパ `_screen_context_block(body, document_id) -> str`

両経路の唯一の実装。順に:

1. `body.screen_context` が無ければ `""`（従来動作）。
2. `normalize_screen_context(payload.model_dump())` — 未知 `screen` / 型不正は `None` → `""`。
3. `str(ctx.selection["document_id"]) != document_id` なら `""`（**他文書の参照で越境させない**。
   要素対話は `ref.document_id`、グラフ対話は解決済み `doc_id` と突き合わせる。要素対話は
   `scope == "document"` のときだけ呼ぶ — 共通部品（domain scope）は論文層を持たない）。
4. `sources = {"paper_layer": build_paper_layer_for_document(doc_id)}` を try/except で組む
   （失敗は `{}`。import は遅延 = route 間の循環 import を避け、テストの monkeypatch も効く）。
5. `render_block(resolve(ctx, sources))` を返す（例外はここでも握って `""`）。

権限は呼び出し側で既に通っている（要素対話 = `_ensure_document_viewable` / グラフ対話 =
`_resolve_graph_document`）。`build_paper_layer_for_document` 自身はゲートを持たない
（GET 側が `_ensure_document_viewable` を通してから呼ぶ、と docstring に明記）。

### 10.5 論文層の穴の是正（§5.2）

- **`agent_component_id` の通し**: `_normalize_stored_component_graph` が正規化ノードに
  `agent_component_id` を additive に載せる（保存グラフに無い旧行ではキー自体を足さない）。
  `persist_component_graph` は `component_id` を DB UUID に差し替えて agent 側 ID をこのキーに
  退避するため、落とすとノードの「論文側の顔」（component 要約・contextual 説明）が全て空になる。
- **保存グラフ不在のフォールバック**: `build_paper_layer_for_document` が
  `_build_component_graph_payload` に落ちたときだけ `extra_facts=[FACT_NO_STORED_GRAPH]` を渡す
  （「理論操作グラフが未構築のため、論文との対応は導出できません（再解析で構築されます）。」）。
  `build_paper_layer` は `extra_facts` を `facts[]` の先頭に重複排除で積むだけで、`available` も
  他の射影も変えない。**通常経路では kwarg 自体を渡さない**ので既存の呼び出し契約は不変
  （既存テストの monkeypatch も壊れない）。

### 10.6 テスト

| ファイル | 追加内容 |
|---|---|
| `tests/test_assistant_context_route.py`（新規・27件） | `TestGraphSessionScreenContext`（一致で BLOCK_HEADER 先頭 / 保存は生発話 / 他文書は無視 / 未知 screen は 200 / 論文層失敗は静かに縮退 / view 事実のみ残る / 無指定は不変 / 429 では論文層を引かない）・`TestElementSessionScreenContext`（同上 + `selected_context` との順序 + domain scope で解決しない）・`TestScreenContextPayload`（切り詰め・extra 拒否・両モデルに field）・`TestRouteWiringGuardrails`（保存 content が生変数・両経路が共通ヘルパ経由・ヘルパが書き込み/監査をしない・CostGate が解決より前） |
| `tests/test_graph_paper_layer_api.py` | `test_fallback_graph_adds_no_stored_graph_fact` / `test_stored_graph_path_passes_no_extra_facts` / `TestBuildPaperLayerForDocument`（GET の委譲・共有ビルダーはゲートを持たない） |
| `tests/test_graph_paper_layer_core.py` | `TestPersistedPipelineShape`（`persist_component_graph` の保存形フィクスチャ = DB UUID の `id`/`component_id` + `agent_component_id` + agent claim ID。要約・説明・式・逐語引用・図表・章・main 集約・背骨を検証し、`agent_component_id` を落とすと顔が消える回帰も固定）・`TestExtraFacts` |
| `tests/test_graph_review_api.py` | `TestStoredGraphAgentComponentId`（通し・不在時はキーを足さない・空白は足さない） |

全 backend スイート green（13,156 passed / 27 skipped）。

### 10.7 core 実装で入れた3つの差分（設計本文との差）

1. **`MAX_THESIS_ROLE_ITEMS = 3` を追加**。§4.3 は「中心命題での役割」に上限を書いていないが、
   SA7（解決器ごとに項目上限を持つ）に合わせて他の部品と同じ扱いにした。
2. **contextual 説明の status ラベルを表にせず個別定数にした**
   （`EXPLANATION_STATUS_APPROVED_LABEL` / `EXPLANATION_STATUS_CANDIDATE_LABEL`）。同じキー集合
   （approved / candidate）の表が `core/deliberation/dialogue.py` に既にあり、表を増やすと
   `test_label_vocab_guardrails` の「黙った分裂」検出に当たるため。
3. **列挙の打ち切りは件数ではなく `／ほか`** で示す（SA4: 件数を書かない）。

