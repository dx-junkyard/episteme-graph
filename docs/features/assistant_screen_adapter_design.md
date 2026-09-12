# 画面文脈アダプター（Assistant Screen Adapter — AI 対話に「いま見ている画面」を渡す層）

> **状態: 実装済み（正本・凍結）**（Phase 1 = グラフレビュー。Phase 2〜3 は §6 予約。
> **Phase 4 = 学習チャットは §11 で実装済み（2026-09-12・4-a/4-b/4-d。4-c は保留）**）
> （2026-09-06 起票・同日実装。migration なし・新テーブルなし・LLM 呼び出し回数不変の
> 読み時解決。実装記録は §10 = Phase 1 / §11.15 = Phase 4）

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

- **Phase 4 は §11 で実装済み**（2026-09-11 起票・2026-09-12 実装。4-a/4-b/4-d）。本表の行は
  要約のままとし、語彙・解決器・予算・段階導入・ガードレール・実装記録の正本は §11 に置く。
- Phase 2（W層要素モーダル）/ Phase 3（Admin Copilot）は予約のまま未着手。



## 7. 非スコープ（v1）
- LLM のツール呼び出し（agentic データ取得）— SA3 で恒久排除。
- 画面状態の保存・履歴化・教員向け集約 — SA6。
- 解決結果からの書き込み（承認・保存）— SA5。
- 画面のスクリーンショット・DOM テキストの送信 — SA1。
- 学習者向け Phase 4 の実装（v1 = Phase 1 時点では設計予約のみ。→ **2026-09-12 に実装** = §11 / §11.15）。

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


---

## 11. Phase 4 — 学習チャットへの構造 grounding

> **状態: 実装済み（2026-09-12）**（2026-09-11 起票）。段階導入（§11.10）のうち
> **4-a（選択テキストの注入）/ 4-b（`element` + `view`）/ 4-d（`verification` +
> `placement`）が出荷済み**で、**4-c（`topic` / `visible`）は保留**（§11.13-3 の推奨どおり
> 4-b の実測後に判断する）。3段の UX ロードマップの第2段
> （Phase 1 = 入口統合 / **本節 = 構造 grounding** / Phase 3 = ストリーミング）。
> migration なし・新テーブルなし・新エンドポイントなし・**LLM 呼び出し回数不変**（SA3）。
> 実装記録は §11.15。本文（§11.1〜§11.14）は設計時の記述で、実装との差分は §11.15 に置く。
>
> **前提の更新（2026-09-12）**: Phase 1（入口統合）は実装済み
> （[learning_chat_entry_unification_design.md](learning_chat_entry_unification_design.md) §13）。
> `_learning_chat_core` は当該往復の**様相（stance）**を解決済みで持つため、本節の
> 「どの様相のとき構造ブロックを足すか／省くか」は新たに推定せず、その解決結果を読めばよい
> （様相は `discuss_scope` / `cycle_mode` / `backstage` を切り替えない = LC1 なので、
> 解決器のスコープ判断は従来どおり明示状態から導く）。

### 11.1 目的と証拠 — パイプラインの構造成果は回答プロンプトに一度も届いていない

学習チャットの grounding は **チャンク（本文）だけ**で組まれている。現物:

- `backend/api/routes/learning.py:3215` — `search_chunks_with_metadata(body.message, top_k=8,
  allowed_document_ids=allowed_document_ids)`。返るのは `text` / `source_title` / `tier` /
  `material_id` で、**構造（claim / component / equation）は1件も含まない**。
- `learning.py:3225` — `cited_chunks.append(f"[現在表示中の教材]\n{topic_material[:5000]}")`。
  表示中トピックの本文を先頭5000字だけ入れる。トピックに紐づく `linked_claim_ids` /
  `linked_component_ids` / `evidence_links` は**参照されない**。
- `learning.py:3263-3267` — `context_block` はこの2種（トピック本文 + `[出典N]` チャンク）
  の連結に `UNTRUSTED_SOURCE_NOTICE` を足したもの。
- `learning.py:3342-3356` — LLM への入力は `[system, user(context_block + 足場), assistant(ack)]`
  + `window_history(body.history, max_messages=20, max_chars=2000)` + `body.message`。
  **この5要素以外に構造は入らない。**

いっぽうパイプラインが作った構造は、**別の UI からしか触れない**:

| 構造 | 学習者が触れる経路 | 回答プロンプト |
|---|---|---|
| `theory_claims` / `theory_components` | 教材本文の ⚓ チップ（`app.js:502-549`、供給元 `core/course_content_builder.py:1356 build_topic_evidence_items`）→ `GET /api/learning/courses/{id}/components/{cid}/context` | ✗ |
| TheoryOperationGraph main 層 | 同 context API の `graph` レーン（`core/component_context.py:492 _build_graph`） | ✗ |
| equation / claim の W層レンズ | `core/element_context.py:618 build_element_context` | ✗ |
| `epistemic_ledger` の検証事実 | `api/routes/doubt.py:2247 get_learner_ledger_line`（出典タブの一行） | ✗ |
| `landscape_placements` | `api/routes/landscape.py:852 get_course_landscape`（分野の地図レイヤー） | ✗ |
| C層 approved explanation / contextual `element_explanations` | 同上・チップ展開 | ✗ |
| discuss 開幕の中心命題・支持構造 | `core/discuss/opening.py:232 project_thesis`（開幕画面のみ） | ✗ |

**学習者の体験**: チップを開けば「この主張はこの式に支えられている」「このコーパスの中では
検証記録がありません」と読めるのに、同じ画面のチャットで同じ要素について尋ねると、AI は
その構造を知らないまま本文チャンクだけで答える。**画面が知っていることを AI が知らない**という、
§1 でグラフレビューについて述べた症状が、学習側にそのまま残っている。

もう一点、**選択テキストが回答に届いていない**。`learning.py:1483-1494`
（`_learner_selected_anchor`）は `body.selection_text` を読んで `evidence_quote` に逐語で
焼き込み、`structure_anchor` として痕跡に記録する（`learning.py:3596`）。だが同じ文字列は
プロンプトに**一切入らない**。`_build_anchor_ladder_hint`（`learning.py:1509-1573`）が
system へ足すのは「区画に注目している」という**ヒント文だけ**で、選択された文そのものは
渡らない。学習者が段落を選んで「ここが分からない」と送ると、AI は「どこか」を知らされずに
top-k 検索の結果で答える。

### 11.2 学習者向け ScreenContext の語彙

Phase 1 の形（§4.1）をそのまま使い、`screen` に `"learning"` を足す（`KNOWN_SCREENS`
に追加。`core/assistant_context/schema.py:28-30`）。

```jsonc
{
  "screen": "learning",
  "selection": {
    "course_id": "…",              // "_doc:{document_id}" センチネルも可（§11.5）
    "topic_id": "…",
    "segment_id": "3",             // 表示中スライド／区画（数値も文字列化して渡す）
    "kind": "element",             // "topic" | "segment" | "chunk" | "element" | "document"
    "element_type": "component",   // component | claim | equation | figure
    "element_id": "…",
    "chunk_id": "…"
  },
  "view": { "mode": "chat", "precision_reading": true, "discuss_scope": "course_sources" },
  "visible_entities": [ { "type": "component", "id": "…", "title": "≤40字" } ]
}
```

- `selection.kind` は解決器の分岐キー。無ければ `element_id` → `chunk_id` → `segment_id` →
  `topic_id` の順に**サーバが推定**する（画面の申告を必須にしない）。
- `view.mode` は既存 `screen_mode`（`voice` | `lecture` | `chat`。`api/schemas.py:340-344`）を
  **そのまま写す**。新しい表示モード語彙を作らない。
- `view.precision_reading` は精読モード（`app.js` の localStorage
  `eg_precision_reading:<courseId>`。UC Phase 2）。**サーバに設定を保存しない**規律は不変で、
  画面が毎回申告する。
- `visible_entities` は**いま描かれている ⚓ チップの id と題名**。`app.js:526` が
  `chunk.evidence_items` から既に `{kind, id, title}` を組んでいるので、新しい抽出処理は要らない。
  本文抜粋を入れない（SA1）。

**`LearningChatRequest` への追加は1フィールドだけ**（`backend/api/schemas.py:290`）:

```python
    # 画面文脈アダプター Phase 4（assistant_screen_adapter_design.md §11）。
    screen_context: ScreenContextPayload | None = None
```

**`selection_text` / `selection_segment_id` は現在位置（`api/schemas.py:335-337`）に残す**（推奨）。
理由は3つ: ①`_learner_selected_anchor` が痕跡記録のためにこの2つを読んでおり
（`learning.py:1483-1494`）、移せば痕跡側の互換を壊す ②`selection_text` は**参照ではなく
逐語テキスト**なので、SA1 の「参照だけ」を守る `screen_context` に混ぜると条項が濁る
③別ブロックとして注入する（§11.4）ため、そもそも同じ袋に入れる必要がない。
`screen_context.selection.segment_id` は `selection_segment_id` の**写し**であってよい
（解決器は両方を見て、食い違えば `selection_segment_id` を優先＝サーバが既に信頼している方）。

### 11.3 解決器（`core/assistant_context/resolvers/learning.py`）

`register("learning", kind, resolver)` を kind ごとに登録する。**登録順がそのまま予算の
優先順位**になる（`registry.render_block` は行境界で末尾から落とす。`registry.py:92-100`）ので、
具体的なものから順に登録する:

| # | kind | 何を解決するか | 呼ぶ学習者射影 | 上限 |
|---|---|---|---|---|
| 1 | `element` | 選択チップ1件の中身 | `component_context.build_component_context` / `element_context.build_element_context` | 1件・事実6行 |
| 2 | `visible` | 画面に出ているチップの**題名と種別だけ** | 射影不要（`visible_entities` の写し） | 8件 |
| 3 | `topic` | 表示中トピックに結ばれた主張・論理要素の要約 | `build_topic_evidence_items` → 各 `component_context` | 主張4・要素3 |
| 4 | `verification` | 台帳の検証事実（閉世界語彙のまま） | `doubt` 学習者射影と同一の投影関数 | 3件 |
| 5 | `placement` | 論文の分野内の位置づけ | `landscape.projection.learner_landscape_dto` | 2件 |
| 6 | `view` | 表示モードの事実1行 | なし | 1行 |

**規律（Phase 1 から継承・学習側で強める）**:

- **生テーブルを引かない**。解決器は `theory_claims` / `theory_components` /
  `epistemic_ledger` / `landscape_placements` に SELECT を書かず、必ず学習者射影を通す。
  射影が持つ遮断（`learner_context_common.strip_confidence:86` の数値除去、
  `is_internal_id_label:264` / `contains_internal_id:307` の内部 ID 遮断、
  `learner_navigable:368` の fail-closed、`scoped_id_match_sql:104` の
  `document_id = ANY(:doc_ids)` 強制）を**再実装しない**。これは Phase 4 の中心規律で、
  ガードレールで固定する（§11.9）。
- **権限は3段**: ①`get_accessible_course_data(user_id, course_id)`（受講・所有・公開テンプレート）
  ②`list_course_source_document_ids(course_data)`（コース sources のみ。`_doc:` 経路は
  `scope_document_ids`）③各射影内の `ANY(:doc_ids)`。route が①②を済ませてから
  `sources` を組み、解決器は**渡された DTO しか見ない**（§3 の core 純関数規律）。
  `selection.course_id` が URL の `course_id` と一致しないときは**丸ごと無視**
  （Phase 1 の document 不一致と同じ扱い。`§10.4` 手順3の学習側版）。
- **数値・内部 ID・生 TeX を出さない**（SA4 + LS4 + PN-4 + PL7）。式は印字番号
  （`eq_2_7` 形は論文の式番号として可読なので通す — `learner_context_common.py:165` の既定裁定）、
  claim は本文の先頭抜粋、component はラベル、図は `figure_label` / caption。
- **出所ラベルを剥がさない**。AI 推定の配置は「AIによる推定（未確認）」、教員確定は
  「教員確認済み」を**事実文の中に含めて**渡す。候補（`status='candidate'`）の説明は
  「候補（未承認）」を付ける（`assistant_context/schema.py:130-131` の定数を再利用）。

**事実文のテンプレ（案）**:

```
- 学習者が選んでいるのは論理要素「〈label〉」で、論文『〈title〉』に由来します
- 〈label〉が前提にしているのは「〈precondition〉」「〈precondition〉」です
- 〈label〉が使う式は 式 (12)・式 (14) です
- 〈label〉を支える主張: 「〈claim 本文の先頭80字〉」
- 〈label〉について、このコーパスの中では検証記録がありません
- 〈label〉の検証は「〈condition〉」の範囲で記録されています
- 論文『〈title〉』は、分野の地図（版 〈v〉）の「〈region〉／〈concept〉」に置かれています（教員確認済み）
- 論文『〈title〉』の位置づけは AIによる推定（未確認）です: 「〈region〉／〈concept〉」
- いま画面には ⚓「〈title〉」「〈title〉」／ほか が出ています
- 学習者は精読モードで、スライド〈n〉を表示しています
```

打ち切りは件数ではなく `／ほか`（`MORE_ITEMS_MARK`。`schema.py:146`）。

**予算**: 既存プロンプトは既にトピック本文5000字（`learning.py:3225`）+ 最大8チャンクを
持つので、教員側の `MAX_BLOCK_CHARS = 2400`（`schema.py:46`）は学習側には過大。
**`MAX_BLOCK_CHARS_LEARNING = 1200`** を別定数で置き、`render_block(facts, *, header=...,
max_chars=...)` を **additive kwarg** で拡張する（既定値は現行のまま = Phase 1 バイト等価）。
ヘッダも学習者向けに別定数を持つ（Phase 1 のヘッダは「教員がいま画面で選んでいる対象」と
書いてあり、そのままでは嘘になる）:

```python
BLOCK_HEADER_LEARNING = (
    "[画面文脈 — 学習者がいま画面で見ている対象について、サーバが解析結果から解決した事実。"
    "根拠ではなく範囲の手がかり]"
)
```

### 11.4 選択テキストの注入

`selection_text` は **`screen_context` とは別の第2ブロック**として、当該ターンにだけ入れる:

```
[学習者が選択した箇所 — 学習者が教材上で範囲選択した逐語。ここについての質問である可能性が高い]
（表示中の教材と一致を確認済み）
> 〈selection_text の逐語・最大600字〉
```

- **サーバ側で一致検査をする**。`_topic_student_material(topic_info)`（`learning.py:3222`）で
  既に取得している表示中教材本文に対する部分文字列一致で、一致すれば
  「（表示中の教材と一致を確認済み）」、しなければ**そのまま載せたうえで**
  「（本文との一致は確認できていません）」と書く。クライアント申告を根拠と区別する
  （SA1 の趣旨をテキストにも適用する。§9 の「渡した瞬間に区別できなくなる」への回答）。
- 出所が PDF 由来の untrusted 入力である点は本文チャンクと同じなので、
  `core.text_hygiene.UNTRUSTED_SOURCE_NOTICE`（`text_hygiene.py:33`）の適用範囲に含め、
  `strip_control_sequences`（`text_hygiene.py:51`）を通してから載せる（TB1〜TB4）。
- **痕跡記録は非改変**。`_learner_selected_anchor`（`learning.py:1463-1494`）も
  `structure_anchor` の返却（`learning.py:3596`）も触らない。注入はプロンプトだけの追加で、
  `evidence_quote` の逐語・`attribution_source='learner_selected'` は現行のまま。
- `_build_anchor_ladder_hint` も**変えない**。ヒント（system の「区画に注目している」）と
  逐語（user ターンの引用ブロック）は役割が違い、片方をもう片方で置き換えない。

### 11.5 プロンプトへの合流点

**当該ターンの user メッセージの先頭に prepend する**（`learning.py:3356` の
`messages.append({"role": "user", "content": body.message})` を
`content=_prefix + body.message` に変える）。順序は
**画面文脈ブロック → 選択箇所ブロック → 発話**（Phase 1 §10.3 と同じ「独立ブロックを先頭に」）。

`context_block`（`messages[1]`）に混ぜない理由: `context_block` は足場ターンで、
`window_history` の窓の**外**にある安定した土台として毎回同一に組み直される部分。画面文脈は
ターンごとに変わる（スライドを送る・別のチップを開く）ので、変わるものを土台に混ぜると
「前のターンの画面」と「いまの画面」が履歴上で見分けられなくなる。

**保存は不変（SA6）**: `persist_chat_history(current_user["id"], course_id, topic_id,
body.history, body.message, result.answer)`（`learning.py:3184-3187`）は `body.message` を
そのまま渡しているので、プロンプト側だけを組み替えれば `learning_chat_history` には
生の発話しか残らない。**`screen_context` を痕跡 payload（`learning.py:3480-3500`）にも
焼き込まない**（観察面を広げない = SA6 / UC4 / PN-1）。

**モード別の扱い**:

| モード | 画面文脈ブロック | 選択箇所ブロック | 根拠 |
|---|---|---|---|
| 通常（on_path / explore） | ○ | ○ | 本節の主対象 |
| `discuss`（`_is_discuss`） | ○ | ○ | 構造を確かめる対話そのもの。スコープは `discuss_scope` の解決結果に従い、**画面文脈が範囲を広げてはならない**（DM1。`selection` が範囲外 document を指していたら無視） |
| discuss / document 直付け（`_doc:` センチネル） | ○ | ○ | `scope_document_ids`（`learning.py:2812`）を document スコープの正本にする。合成 course_data から sources を引き直さない |
| `casual`（🤖 音声・気軽モード） | **✗（推奨）** | ○ | casual は短い会話調が仕様で、事実列挙は文体と衝突する。学習者が明示的に選んだ箇所だけは渡す |
| `cycle_mode="elicit"` | **✗（推奨）** | ○ | Elicit は「答えを提示せず予測を引き出す」（`learning.py:1368-1371`）。主張本文・検証事実を渡すと**問いの答えを手渡す**ことになる。表示モードの事実1行のみ許す |
| `cycle_mode="diff"` | ○ | ○ | Diff は「本人の予想と骨格の差分」（`learning.py:1393-1396`）であり、骨格側の事実がないと並置できない |
| `backstage`（楽屋） | ○ | ○ | 楽屋は記録面の私有化であって grounding の縮退ではない（`structure_descent_design.md`）。記録側の除外は現行のまま |
| `check_scaffold`（確認問題の壁打ち） | ○ | ○ | 「解答の直接提示禁止・構成要素の説明は可」（`learning.py:3300-3301`）と両立する。要素の説明材料は増えてよい |

**R層の伏せフィールドは構造的に届かない**。`core/reconstruction/schema.py:40` の
`HIDDEN_CLAIM_FIELDS = ("text", "normalized_text", "equation", "evidence_text")` と
`response_space` / `expected`（同 78-79 / 95-96）は `reconstruction_items` の列であり、
本節の解決器は `reconstruction_*` テーブルを**一切読まない**（§11.3 の「学習者射影のみ」）。
ただし elicit モードでは**同じ意味の情報が claim 本文経由で漏れる**ので、上表のとおり
モード単位で遮断する。ガードレールは両方を検査する（§11.9）。

**版ピンとの関係**: 参照集合（どのトピックにどのチップが出るか）は
`_apply_course_version_view`（`api/services.py:288, 612`）が返す**版ピン済みコース
スナップショット**から来る。いっぽう解決した中身（component 要約・claim 本文・台帳）は
**live テーブル**から読む — V層は document 成果物のピン凍結ブラウズを v1 で実装していない
（CLAUDE.md V層「既知の限界」）。これは既存の `/components/{id}/context` API と**同じ
意味論**で、`component_context` が `provenance="course_freeze"` を名乗りながら本体は live を
読んでいる状態そのものである。Phase 4 は**この意味論を変えない**（新しい凍結規約を発明しない）。
食い違いは、参照が版に無い＝チップが画面に出ない＝解決対象にならない、という形で自然に閉じる。

### 11.6 LLM 回数・コスト

- **追加 LLM コールはゼロ**（SA3）。1ターン1コールのまま、入力トークンだけが増える。
- CostGate（`LEARNING_CHAT_MAX_CALLS_PER_DAY`）の消費位置は不変（`learning.py:2830` 近傍の
  「最初に LLM を呼ぶ直前に1回だけ」）。**解決は CostGate より後・LLM 呼び出しより前**に置き、
  429 で返るリクエストでは解決を走らせない（Phase 1 のテスト
  `test_assistant_context_route.py::…429 では論文層を引かない` と同型）。
- DB 読みは有界: 選択要素1件の射影（1〜3クエリ）+ トピック要素の射影（上限7件）+
  台帳3件 + 配置1クエリ。**キャッシュはリクエスト内のみ**（`sources` dict に載せて解決器へ
  渡す。プロセス跨ぎのキャッシュを作らない）。
- 失敗は fail-soft: 射影1本の例外はその facts だけ欠け、全体が空なら
  `render_block` が `""` を返して**従来と同一のプロンプト**になる（`registry.py:60-66, 86`）。

### 11.7 観測

Phase 4 の価値（構造 grounding が回答を変えたか）を後から測るために、**種別だけ**を記録する:

- `discuss_metric_events` に `structured_grounding_present`（`core/discuss/observation.py:327`
  近傍の語彙表に1語追加）。payload は**常に空**（DO1: 本文非含有）。どの kind の解決器が
  facts を出したかも payload に入れない — 出したか出さなかったかの1ビットに留める。
- 痕跡（`interest_traces`）には**焼き込まない**。`screen_context` は保存しない（SA6）ので、
  「構造が渡ったターン」を痕跡側から復元できる状態も作らない。
- **学習者には何も見せない**（DO3 / IG3）。指標カタログ（`core/indicator_catalog.py`）へ
  1件足すかは、実際に集計 API を教員・管理者に出すときに判断する（IG4: 集約を見せる経路を
  足したらカタログにも足す。出さないなら足さない）。

### 11.8 フロント（`app.js`）

- `window.LearningScreen.getScreenContext()`（または `app.js` 内のローカル関数）を
  `GraphReview.getScreenContext()`（`admin-graph-review.js:1755-1771`）と**同じ形**で作る。
  戻り値は ID・種別・題名だけで、`display_text` / チャンク本文 / 選択本文を**入れない**
  （`selection_text` は従来どおり独立フィールド）。
- 材料はすべて既存。チップは `app.js:526` が `chunk.evidence_items` から
  `{kind, id, title}` を組んでおり、ラッチ中アンカーは `data-evidence-ref`
  （`app.js:4629-4643`）、スライドは `position_anchor.segment_id`、精読モードは
  localStorage。**DOM のテキストを読む処理を新規に書かない**（静的 grep で固定）。
- 送信は全経路（テキスト送信・🤖 音声ループ・チップからの質問・discuss）で同じボディを
  使う。グラフレビューが `sendChatText` に一本化して音声も自動的に同じボディになったのと
  同じ形（`admin-graph-review.js:1833`）にする。
- **1画面レイアウト規律は無関係**（`.mn` の `overflow: clip` / 下段 `flex: 0 0 auto`。
  CLAUDE.md 開発ルール5）。本節は DOM を増やさないので
  `test_learning_layout_static.py` に影響しない。

### 11.9 ガードレール案

| テスト | 固定する内容 |
|---|---|
| `test_assistant_context_learning_core.py` | 解決器が入力を mutate しない／数値（`graph_paper_layer.schema.FORBIDDEN_KEYS:90-95`）を出さない／内部 ID（`ev_` / `synth_` / `claim_` / `span_` / `support:` / `node_` / UUID）を事実文に出さない／`MAX_BLOCK_CHARS_LEARNING` を超えない／空 facts で `""` |
| `test_assistant_context_learning_guardrails.py` | **AST 検査**: `resolvers/learning.py` が `theory_claims` / `theory_components` / `epistemic_ledger` / `landscape_placements` / `reconstruction_items` を含む SQL 文字列を持たない・`sqlalchemy` / `fastapi` / `core.llm` を import しない。学習者射影（`learner_context_common` 由来の遮断）を通さない経路が無いこと |
| 同上 | 閉世界語彙（SL1）: 台帳由来の事実文に「この分野では」「誰も検証していない」「未踏」「世界初」が現れない（`test_stakes_ledger_guardrails.py` の denylist を再利用） |
| `test_assistant_context_learning_route.py` | `screen_context` が `learning_chat_history` に永続化されない（保存 content が生 `body.message`）／痕跡 payload にも入らない／`selection.course_id` 不一致は無視／`cycle_mode="elicit"` で構造 facts がゼロ／`casual` で構造 facts がゼロ／`screen_context` 無しの LLM 入力が従来とバイト等価／CostGate 消費が解決より前・429 では解決しない |
| 同上 | R層の伏せフィールド（`HIDDEN_CLAIM_FIELDS` + `expected` + `response_space`）が LLM 入力に現れない |
| `test_mirroring_prompt_guardrails.py:123` / `test_discuss_mode.py:276` | **既存**: `window_history(body.history, max_messages=20, max_chars=2000)` の逐語が `learning.py` に残ること。本節は `messages.append` の直前だけを触るので、この行は不変のまま通る |
| `test_learner_ux_static.py` 系 | `app.js` の `getScreenContext` が本文フィールド（`display_text` / `text` / `innerText` / `textContent`）を参照しない（静的 grep） |
| `test_search_visibility.py` | **既存**: 可視性 fail-closed。解決器は `allowed_document_ids` を独自に組み直さないこと（route が渡した集合の写しであること）を追加検査 |

### 11.10 段階導入（各段が単独で出荷可能）

| 段 | 内容 | 追加する解決器 | 触るファイル |
|---|---|---|---|
| **4-a** | 選択テキストの注入だけ（`screen_context` 不要） | なし | `learning.py`（prepend 1箇所）+ テスト |
| **4-b** | 選択チップ1件の解決 | `element` / `view` | + `schema.py`（`"learning"` / 新ヘッダ / 新上限）/ `resolvers/learning.py` / `schemas.py` / `app.js` |
| **4-c** | 表示中トピックの主張・要素の要約 | `topic` / `visible` | + `build_topic_evidence_items` の再利用 |
| **4-d** | 検証事実・分野内の位置づけ | `verification` / `placement` | + 台帳・配置の学習者射影の呼び出し |

**後方互換**: `screen_context` を送らないクライアント（および 4-a 適用前に
`selection_text` を送らないターン）は、`render_block` が `""` を返すため
**プロンプトが1バイトも変わらない**。旧フロントと新バックエンドの組み合わせで挙動が
変わらないことをテストで固定する（Phase 1 の「無指定は不変」と同型）。

### 11.11 非スコープ（Phase 4 v1）

- **LLM のツール呼び出し**（AI が必要に応じて構造を取りに行く）— SA3 で恒久排除。
- **教員限定の事実**: `review_status` の内訳・却下履歴・gap 判断・`decision_context`・
  疑義の投稿者・`recorded_by`。学習者射影が既に落としているものを解決器で復活させない。
- **数値**: cosine・confidence・支持経路の本数・配置件数・負荷度・k-匿名レンジ。
- **他人の痕跡**: 別の学習者の tension / 問い / 再構成の成否（PN-1）。
- **推定能力による自動適応**: 「この学習者は基礎が弱いので構造を減らす」の類（UC5 / UC7）。
  本節が変えるのは**どの事実が入力に載るか**であって、**学習者をどう見積もるか**ではない。
  画面の申告（何を選んでいるか）以外を適応の入力にしない。
- **ストリーミング**（ロードマップ Phase 3 の主題）・**入口統合**（Phase 1）。
- **G層 To-Do・バッジ・学習者への通知**（押し付けない = 原則12）。

### 11.12 vision §6（14原則）照合表

| # | 原則 | Phase 4 での守り方 |
|---|---|---|
| 1 | AIは候補まで・確定は人間 | 解決器は事実を渡すだけで確定を作らない。候補（`status='candidate'`）の説明は「候補（未承認）」ラベル付きで渡す（SA5） |
| 2 | evidence-based | 渡すのは出典側の逐語・印字番号・ラベル。選択テキストは**サーバが本文と突き合わせて**一致の有無を明示（§11.4） |
| 3 | 情報を落とさない | 読み取り専用。落とすのは表示上限のみで、打ち切りは `／ほか` で正直に示す |
| 4 | 数値の用途と粒度を統治 | 学習者に数値を出さない（`strip_confidence` / `FORBIDDEN_KEYS`）。観測は1ビットのみで指標カタログの追加も出す時だけ（§11.7） |
| 5 | 監視しない | `screen_context` を保存しない・痕跡に焼かない・他人の痕跡を渡さない |
| 6 | egocentric のみ | 渡すのは常に「いま見ているものの周り」。コーパス全体の俯瞰を作らない |
| 7 | リンクであってマージではない | 射影の DTO をそのまま事実文にする。本文を書き換えて要約しない（抜粋は先頭 n 字の決定論切り出し） |
| 8 | 出所の正直さ | 「AIによる推定（未確認）」「教員確認済み」「候補（未承認）」を剥がさない。台帳は SL1 の閉世界語彙のまま |
| 9 | 同期パスに LLM を入れない | 解決は決定論・非LLM（SA3）。失敗は空ブロックへ縮退し、回答は従来どおり出る |
| 10 | 完了フラグを持たない | 読み時導出のみ。解決結果を保存しない |
| 11 | fail-closed | 権限3段（受講 → コース sources → `ANY(:doc_ids)`）。course_id 不一致は無視 |
| 12 | 押し付けない | 新しい UI・バッジ・自動表示を作らない。渡すのは学習者が**既に画面で選んでいるもの**だけ |
| 13 | 層は積層し、下層を改変しない | 学習者射影・W層・A層を読むだけ。`learning.py` への変更は prepend 1箇所と optional フィールド1つ |
| 14 | 監査必須・帰属必須 | 状態変更が無いので記帳対象が無い（SA5）。記帳しない理由を本表に明示する |

### 11.13 オーナー判断が要る点

1. **AI 推定の配置（`status='inferred'`）を回答プロンプトに載せてよいか**。
   出典タブは既に「AIによる推定（未確認）」ラベル付きで学習者に見せている
   （`landscape.py:852` の `LEARNER_VISIBLE_STATUSES`）ので、**載せる（推奨）**。ただし
   画面に出ているものと AI が語るものでは、後者のほうが断定に聞こえやすい。
   → **推奨: 載せる。ただしラベルを事実文の中に含め、剥がれていないことをガードレールで固定する。**
   → **採用（§11.15）**: `resolve_placement` が `provenance_label` を事実文の括弧内に載せ、
   `test_assistant_context_learning_guardrails.py::TestProvenanceLabelsSurvive` が固定する。
2. **SL1 の閉世界語彙は、生成プロンプトを通しても保てるか**。いまの SL1 は
   **サーバが書く文字列**に対する denylist で守られている。事実文として
   「このコーパスの中では検証記録がありません」を渡すと、LLM が
   「この分野ではまだ誰も検証していません」と言い換える余地が生まれる — denylist は
   出力側に掛かっていない。
   → **推奨: 載せる。ただし `out_of_source_guard_instruction()`（`learning.py:3303`）と
   同型の固定指示文を1本足し（「検証記録の不在について言えるのは、このコーパスの中では、
   までである」）、その指示文の原文存在をガードレールで固定する。** 不変条項の解釈に
   関わるので、この2段構え（事実 + 出力側の拘束）でよいかをオーナー確認。
   → **採用（§11.15）**: 拘束文は `LEARNING_VERIFICATION_OUTPUT_CONSTRAINT`
   （`core/assistant_context/schema.py`）。**台帳由来の事実が実際にブロックへ載ったターンだけ**
   system 末尾に足す。**禁止語を例示せず肯定形だけで書いた**（理由は定数のコメント —
   本モジュールが SL1 denylist の走査対象であることと、表層形のプライミング回避）。
3. **4-c（学習者が尋ねていないのに、表示中トピックの主張要約を毎ターン渡す）は
   「押し付けない」（原則12）に触れるか**。画面表示は変わらず AI の知識だけが増えるので
   触れないと読むが、AI が自発的に構造の話を始めるようになれば体験としては変わる。
   → **推奨: 4-b（明示選択のみ）までを先に出荷し、4-c は 4-b の実測後に判断する。**
   → **採用（§11.15）**: 4-c は保留。`topic` / `visible` の解決器は**関数ごと存在しない**
   （ガードレールが AST で不在を固定する）。判断材料は §11.7 の
   `structured_grounding_present` の実測。

### 11.14 migration

**不要**。新テーブル・新列・語彙の CHECK 変更は無い。追加は
①`LearningChatRequest.screen_context`（optional）②`KNOWN_SCREENS` への `"learning"`
③`render_block` の additive kwarg ④`discuss_metric_events` の event 語彙1語
（同テーブルの `event` は CHECK ではなくアプリ側の語彙表 — `observation.py:327` 近傍）。
本節では**想定 migration 番号を書かない**（`docs/development_checklist.md` §5）。

### 11.15 実装記録（2026-09-12）

**範囲**: §11.10 の **4-a（選択テキストの注入）/ 4-b（`element` + `view`）/ 4-d
（`verification` + `placement` — 選択要素に紐づく分だけ）**。**4-c（`topic` / `visible`）は
保留**（§11.13-3 の推奨どおり、4-b の実測後に判断する）。migration なし・新テーブルなし・
新エンドポイントなし・**LLM 呼び出し回数不変**（1ターン1コール・CostGate 消費位置も不変）。

#### 11.15.1 ファイル

| 層 | ファイル | 内容 |
|---|---|---|
| core | `backend/core/assistant_context/schema.py` | `SCREEN_LEARNING` / `KNOWN_SCREENS` への追加 / `BLOCK_HEADER_LEARNING` / `MAX_BLOCK_CHARS_LEARNING` / 選択ブロックの定数（ヘッダ・一致2文言・逐語上限）/ `LEARNING_VERIFICATION_OUTPUT_CONSTRAINT` / 学習側の項目上限・語彙表 / `infer_selection_kind()` |
| core | `backend/core/assistant_context/registry.py` | `render_block(..., *, header, max_chars)` と `resolve(..., *, kinds)` の **additive kwarg**（既定は Phase 1 とバイト等価） |
| core | `backend/core/assistant_context/selection.py`（新規） | `render_selection_block(selection_text, material_text)` — 制御文字除去 → 600字 → 空白正規化の部分文字列一致で「一致を確認済み」／「一致は確認できていません」を**必ず併記**（逐語は原文のまま・不一致でも落とさない） |
| core | `backend/core/assistant_context/resolvers/learning.py`（新規） | 4解決器 `element` / `verification` / `placement` / `view`。`learner_context_common` からは **`contains_internal_id` / `is_internal_id_label` の純関数だけ** import する |
| route | `backend/api/schemas.py` | `ScreenContextPayload` の移設（Phase 1 = W層と Phase 4 = 学習チャットの共通の受け口）+ `LearningChatRequest.screen_context`（optional） |
| route | `backend/api/routes/deliberation.py` | `ScreenContextPayload` を `schemas` から再エクスポート（定義を二重化しない・import 面は不変） |
| route | `backend/api/routes/learning.py` | `_screen_selected_element_type` / `_screen_element_sources` / `_screen_element_document_id` / `_learning_screen_sources` / `_learning_screen_context_block` + 合流点1箇所 + `_component_context_with_explanation` の抽出 |
| route | `backend/api/routes/doubt.py` | `learner_ledger_line()` の抽出（`get_learner_ledger_line` は1行委譲・挙動不変） |
| route | `backend/api/routes/landscape.py` | `learner_landscape_for_documents()` の抽出（`get_course_landscape` は `course_id` を付けて返すだけ・挙動不変） |
| core | `backend/core/discuss/observation.py` | `METRIC_EVENT_VOCAB` に `structured_grounding_present` |
| core | `backend/core/disclosure_axes.py` | `learning_chat` の `external_transfer` / `ai_touchpoints` / `basis` を現物に合わせて更新（§11.15.7） |
| front | `frontend/public/js/app.js` | `getScreenContext(payload, latchKind)` + `window.LearningScreen` + `sendMessage` の送信ボディ1箇所 + ラッチの生 `kind` 保持 |

#### 11.15.2 エンドポイント（新設ゼロ・optional フィールド1つ）

`POST /api/learning/courses/{cid}/topics/{tid}/chat`（および同じコアを通る
`POST /api/learning/documents/{ref}/discuss/chat`）に `screen_context:
ScreenContextPayload | None` を足しただけ。**未指定・未知 `screen`・`selection.course_id`
不一致では、LLM 入力が従来とバイト等価**（`render_block` が `""` を返す）。
`ScreenContextPayload` の切り詰め規約（`screen` 40字 / `visible_entities` 20件 /
`id` 160字 / `title` 40字・**422 にしない**）は §10.2 のまま。

#### 11.15.3 合流点と prepend の順序

合流点は `_learning_chat_core` の **`_consume_quota()` の直後・`generate_text` の前**1箇所。
当該ターンの `messages[-1]` を

```
画面文脈ブロック（BLOCK_HEADER_LEARNING）
↓
選択箇所ブロック（SELECTION_BLOCK_HEADER + 一致の明示 + 引用）
↓
body.message（発話）
```

に組み替える（§11.5 の設計どおり。足場ターン `messages[1]` には混ぜない）。
両ブロックは PDF 由来の untrusted 入力（逐語・claim 抜粋）を運ぶので、足場ターンに
`UNTRUSTED_SOURCE_NOTICE` が無いターン（`cited_chunks` が空）では当該ターンの先頭に同じ
注意書きを1回だけ添える（TB1〜TB4・§11.4。足場に既にあれば重複させない）。
**保存と痕跡は不変**（`persist_chat_history` は `body.message` を受け取ったまま・痕跡 payload に
`screen_context` のキーを足さない = SA6）。台帳由来の事実が**実際にブロックへ載ったとき**だけ、
`messages[0]`（system）の末尾に `LEARNING_VERIFICATION_OUTPUT_CONSTRAINT` を足す。
ブロックが非空のターンだけ観測イベント `structured_grounding_present` を**サーバ側で**記録する。

**権限は3段**（§11.3 の設計どおり）: ①呼び出し元が解決済みの `course_data`（受講ゲート）
②`scope_document_ids` があればそれ・無ければ `list_course_source_document_ids(course_data)`
③各学習者射影の内部 SQL の `ANY(:doc_ids)`。**discuss の `all_visible` でも広げない**（DM1）。
`screen_context` を送ってこないリクエストでは `list_course_source_document_ids` すら呼ばない
（クエリを1本も増やさない）。

#### 11.15.4 モード別（§11.5 の表の実装）

| 様相・モード | 画面文脈ブロック | 選択箇所ブロック | 実装 |
|---|---|---|---|
| 通常（tutor / on_path / explore） | ○ | ○ | `kinds=None`（全解決器） |
| `discuss`（`_doc:` 直付けを含む） | ○ | ○ | 同上。スコープは `scope_document_ids` のまま |
| `casual` | ✗ | ○ | `_is_casual` のとき `_learning_screen_context_block` を**呼ばない** |
| `cycle_mode="elicit"` | 表示モードの1行だけ | ○ | `kinds=("view",)` — `_learning_screen_sources` も**DB を1本も引かない** |
| `cycle_mode="diff"` / `backstage` / `check_scaffold` | ○ | ○ | `kinds=None` |

選択箇所ブロックは様相に依らず常に組む（学習者が明示的に選んだ箇所だけは渡す）。

#### 11.15.5 設計本文との差分

1. **`kinds` は sources の組み立てにも伝播させた**。§11.5 は解決器側の絞り込みだけを書いて
   いたが、`kinds=("view",)` のときに射影を引いてから捨てるのは無駄で、かつ「elicit では
   主張本文を**読みにも行かない**」ほうが条項として強い。`_learning_screen_sources` が
   `kinds` を見て早期 return する。
2. **`_screen_has_ledger` は「ブロックに載ったか」で判定する**。`sources` に台帳があるか
   ではなく、`kinds=("verification",)` で再解決した事実文が**描画済みブロックに含まれるか**を
   見る（予算超過で末尾から落ちた場合・`kinds` で外した場合を「載っていない」に含めるため）。
   再解決は純関数で DB を引かない。
3. **`segment_id` の申告条件をフロント側で絞った**。§11.2 は「`selection_segment_id` の写し」
   としか書いていないが、通常チャットでは `currentAnchor().segment_id` が常に 0 を返すため、
   そのまま載せると `kind` が永遠に `segment` になる（嘘になる）。申告するのは
   ①「ここについて質問」の明示選択 ②レクチャー再生中の表示スライド、の2つだけ。
   サーバ側は §11.2 のとおり `selection_segment_id` があれば画面申告の `segment_id` を
   それで上書きする（`dataclasses.replace` で新しい `ScreenContext` を作る。入力は mutate しない）。
   レクチャー中の表示スライド番号だけはクライアント申告のまま事実文に載る（表示状態の申告で
   あってコーパス由来の事実ではない）。
4. **`ScreenContextPayload` を `api/schemas.py` へ移した**。Phase 1 では
   `routes/deliberation.py` に定義していたが、学習チャットの DTO が `schemas.py` にあるため
   共通の受け口をそこへ置き、`deliberation.py` は再エクスポートだけにした（import 面は不変）。
5. **学習者射影を2本、route から関数抽出した**（`learner_ledger_line` /
   `learner_landscape_for_documents`）。§11.3 の「遮断を2箇所に書かない」を満たすための抽出で、
   既存エンドポイントは委譲に置き換え**挙動は不変**。加えて `learning.py` 内で
   `_component_context_with_explanation`（GET context と画面文脈の共通経路）を抽出した。
6. **`element_type` の語彙吸収**。フロントの `structure_anchor` 語彙（`formula` 等）では
   component と claim の区別が落ちるため、ラッチ元の生 `kind`（`data-evidence-ref` の前半）を
   種別解決に使い、`formula → equation` はフロント・サーバの両方で吸収する（旧クライアント対策）。
   どちらでも判別できなければ**種別を名乗らない**（推測で埋めない）。
7. **`visible_entities` は受けるが解決しない**。フロントは契約どおり送る（SA1 の参照形）が、
   4-c 保留のためサーバは正規化して捨てる。**事実文には一切現れない**ことをガードレールが固定する。
8. **claim 本文の抜粋は 80 字の決定論切り出し**（`MAX_LEARNING_CLAIM_EXCERPT_CHARS`）。
   要約しない（原則7）。
9. **contextual 説明の status ラベル**: 学習者向け component 文脈 DTO の
   `instance.explanation` は route が **`teacher_approved` の説明だけ**を充填し `status` キーを
   持たないため、**status 不在は「承認済み」**とし、`candidate` / `pending` / `draft` /
   `review_required` が明示されたときだけ「候補（未承認）」を付ける（未承認を承認済みと
   言う側の誤りを構造的に防ぐ）。

#### 11.15.6 オーナー判断（§11.13）の採否

1. **AI 推定の配置を載せる** — 採用。`provenance_label`（「AIによる推定（未確認）」／
   「教員確認済み」）を事実文の括弧内に含めたまま渡す。
2. **台帳の事実 + 出力側の拘束の2段構え** — 採用。ただし拘束文は**禁止語を例示せず肯定形だけ**で
   書いた（§11.13-2 の追記と定数のコメント）。拘束が付くのは事実が実際に載ったターンだけ。
3. **4-c は保留** — 採用。解決器を書かず、不在をガードレールで固定した。

#### 11.15.7 開示（可視性6軸）の追随

`core/disclosure_axes.py` の `learning_chat` を現物に合わせた: `external_transfer` に
「範囲選択した文」「画面で開いている要素についてサーバが組み立てた事実（要素の名前・関係する
式や主張・検証記録の有無・分野の地図での位置）」を追記し、`ai_touchpoints` の
`learning:cycle_elicit` と `learning:cycle_diff` を**別項目に分けた**（elicit は構造ブロックを
受け取らないため、同じ説明文でまとめると嘘になる）。`basis` に
`core/assistant_context/resolvers/learning.py` を追加。軸・`label` は変えていない。
学習者マニュアル（`docs/manual/student/02-student.md` §18）にも同じ事実を1文足した。

#### 11.15.8 テスト

| ファイル | 固定する内容 |
|---|---|
| `backend/tests/test_assistant_context_learning_core.py` | 4解決器の事実文・上限・出所ラベル・入力非 mutate・数値/内部 ID 非漏洩・`render_block` の学習側ヘッダと予算（Phase 1 既定とのバイト等価も）・選択ブロックの一致判定・`infer_selection_kind` |
| `backend/tests/test_assistant_context_learning_guardrails.py` | 新モジュールの推移的純粋性（fastapi / sqlalchemy / `core.llm` 非 import）・成果テーブル名や SQL 文字列の不在・書き込み動詞の不在・予算がコード定数であること・**登録 kind が `element` / `verification` / `placement` / `view` の4つだけ**・`resolve_topic` / `resolve_visible` が**関数として定義されていない**（AST）・`visible_entities` が事実文に出ないこと・SL1 denylist・拘束文の原文存在と「拘束文自身が禁止語を含まない」こと・出所ラベルの残存 |
| `backend/tests/test_assistant_context_learning_route.py` | 後方互換（無指定でバイト等価・射影を1本も呼ばない）・`course_id` 不一致の無視・prepend 順・スコープ強制（`all_visible` でも広げない・空スコープは何も解決しない）・モード別（casual / elicit / diff / backstage / check_scaffold）・保存 content と痕跡 payload の非汚染・CostGate（429 では解決しない）・fail-soft・観測イベント・R層の伏せフィールドが届かないこと |
| `backend/tests/test_learning_screen_context_ui_static.py` | `window.LearningScreen` の契約・キー集合と語彙・`segment_id` の申告条件・送信ボディ1箇所・**本文フィールドを読まない**（静的 grep）・題名40字と件数上限・重複排除・例外を出さない |
| 追随（既存） | `test_assistant_context_guardrails.py`（`KNOWN_SCREENS` の各画面に解決器が登録されていること）/ `test_discuss_observation.py`（語彙表）/ `tests/api/test_doubt.py`（記帳者 ID 非開示の検査対象を `learner_ledger_line` へ） |

件数の正本は上記テストファイル（本文に数を書き写さない）。

#### 11.15.9 既知の限界と次の段

- **配置の事実は選択要素の document が特定できたときだけ載る**。現行フロントは
  `selection.document_id` を送らないため、component は射影 DTO（`instance.in_paper.document.id`）
  から常に取れるが、claim / equation / figure は **evidence item に `document_id` があるとき**
  に限られる。取れなければ配置の事実は**黙って出ない**（「見えないものがある」と言わせない = SA2）。
- **版ピンとの意味論は §11.5 のまま不変**。参照集合は版ピン済みスナップショット・解決した中身は
  live テーブルで、既存の `/components/{id}/context` と同じ（新しい凍結規約を発明していない）。
- **次の段**: 4-c（`topic` / `visible`）の可否は `structured_grounding_present` の実測で判断する
  （§11.13-3）。ロードマップの Phase 3 = ストリーミング
  （[llm_response_streaming_design.md](llm_response_streaming_design.md)）が次。
