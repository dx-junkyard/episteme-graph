# 教員指示付き図再解析（Guided Figure Re-analysis）設計書

Issue: （起票時に付番）
Status: 実装済み（2026-07-17。本ファイルが正本。Phase 2＝§9 は未実装）
関連: `docs/features/image_pipeline_knowledge_library_design.md`（L層）,
`docs/features/element_deliberation_workspace_design.md`（W層）, Issue #496（図分類）

---

## 0. 背景と課題

「深く検討」の図ワークスペース（W層, `deliberation.js`）では、教員は
①表示分類（`reviewed_mode`）の上書き ②素の AI 再解析
（`POST /figures/{figure_id}/reanalyze`、ボディなし）③候補の確定/却下、しかできない。

vision 解析が **特定の要素の検出に失敗した場合**（例: 図中の小さな部品を見落とす、
ラベルと本文の対応を取り違える）、教員がそれを **指示して再解析させる手段がない**。
現状の再解析は同じ入力の再実行であり、同じ失敗を繰り返しやすい。

本設計は再解析に2つの教員入力チャネルを追加する:

1. **focus_bbox（領域指定）** — 原図上に矩形を描き「この領域を重点的に見よ」と指示する
2. **hint_text（言葉の指示）** — 「左下の EOM と書かれた箱が変調器。§3.2 の説明に対応する」
   のような自然言語の注意誘導

なお「図↔周辺説明文の結びつけ」自体は既存の
`core/document_pipeline/figure_context.py::collect_figure_context()`
（caption 直近 → Fig.N 参照メンション → 同一セクション本文 → 略語辞書、非LLM・決定論的）
が担っており、本設計はこれを**置き換えない**。教員指示はこの自動収集の**上に載る**
注意誘導である（ブロック単位の明示紐づけは Phase 2、§9）。

## 1. 不変条項（L層の原則を継承）

- **GF1 指示は注意誘導であって確定ではない**: 教員指示付きでも LLM 出力は従来どおり
  candidate / `review_status='review_required'` 系。`source_backed` を自動付与しない。
  確定は既存の候補確定（annotation commit）フローのみ。指示したのが教員本人でも、
  **出力の確定はレビュー操作として別に行う**（指示≠承認）。
- **GF2 evidence の一線を守る**: `evidence_quote` は従来どおり caption / nearby_text の
  逐語のみ。**教員の hint_text を evidence_quote として引用させない**（プロンプトで明示禁止）。
  指示に基づく判断は `guidance_note` / `reason` に書く。role の逐語根拠ルールも不変。
- **GF3 情報を落とさない（P4）**: 指示された要素が見つからなかった場合も
  「見つからなかった」ことを `guidance_note` に正直に残す（無言で無視しない）。
  focus_bbox 指定時も `inner_labels` を除去フィルタせず、focus 内ラベルを**追加提示**する。
- **GF4 決定論的グラウンディング維持**: 部品の `bbox` / `expanded_name` は従来どおり
  `label_ref` → `inner_labels` 突合のみ（`_attach_label_grounding`）。教員の focus_bbox を
  部品の bbox として直接採用しない（focus は入力であって出力座標の根拠ではない）。
- **GF5 監査必須・出所の正直さ**: hint_text / focus_bbox を `theory_review_events`
  （既存 `AUDIT_ENTITY_FIGURE_PRESENTATION`、action=`figure.analysis.reanalyze`）の payload に
  記録する。生成された候補 annotation の body にも guidance を明記し、レビュー UI で
  「教員指示付き再解析による候補」であることが分かるようにする。
- **GF6 コスト上限は既存に同乗**: 新しい環境変数・上限を作らない。既存 CostGate
  （session: 3回/図・教員、daily: `APPARATUS_MAX_CALLS_PER_DAY`）をそのまま使う。
- **GF7 バッチパイプライン非改変**: `orchestrator.py` 経由の一括解析（`analyze_images`）は
  guidance なしで従来どおり動く。新フィールドは全て Optional・default 値付きで、
  既存の呼び出し・エクスポート済み artifact の round-trip を壊さない（migration 不要）。

## 2. 全体データフロー

```
教員（深く検討モーダル）
  ├─ 原図上で矩形ドラッグ → focus_bbox（画像内相対座標 0..1）
  └─ 指示テキスト入力（任意・≤2000字）
        ↓ POST /api/admin/documents/{id}/figures/{fid}/reanalyze  body={hint_text, focus_bbox}
backend/api/routes/figure_presentation.py（検証・権限・監査）
        ↓
backend/core/figure_reanalysis.py::reanalyze_figure(..., guidance=...)
  ├─ focus_bbox 正規化・検証
  ├─ 原図バイトから focus 領域をクロップ（PyMuPDF）→ 第2画像（拡大クロップ）
  ├─ focus_bbox をページ座標へ変換 → 交差する inner_labels を「focus内ラベル」に列挙
  └─ FigureImageInput に guidance を載せて ApparatusSemanticsAgent.run()
        ↓
prompt.py「## Teacher Guidance」+ 第2画像 + focus内ラベル
        ↓ vision LLM（structured output）
ApparatusRecord（+ guidance_note）→ 従来どおり candidate annotation 化・persist_suggestions
```

## 3. 座標系の規約

3つの座標系が関わる。変換の正本はすべてバックエンド（`figure_reanalysis.py`）に置く。

| 座標系 | 用途 | 例 |
|---|---|---|
| **画像内相対 (0..1)** | フロント⇄API の focus_bbox。表示画像上のドラッグ矩形をそのまま正規化 | `[0.1, 0.6, 0.45, 0.95]` |
| **画像ピクセル** | クロップ（相対 × 画像 width/height） | バックエンド内部のみ |
| **ページ座標** | `document_figures.bbox` / `inner_labels[].bbox` との突合 | 既存資産の座標系 |

相対 → ページ座標: `page_x = fig_bbox.x0 + rel_x * (fig_bbox.x1 - fig_bbox.x0)`（y も同様）。
これは admin.js 図モーダルの規約「%座標 = ページ座標を図 bbox で正規化
（region_render / embedded 両方式で同一変換）」の**逆写像**であり、同じ前提を共有する。
`document_figures.bbox` が NULL の図（bbox 推定に失敗した embedded 抽出等）では
ページ座標変換ができないため、**focus内ラベルの列挙はスキップ**し（プロンプトに
「focus region labels: unavailable」と正直に書く）、クロップ（画像ピクセル系のみで完結）と
hint_text は通常どおり機能させる（fail-soft、GF3）。

## 4. API 変更

### 4-1. `POST /documents/{document_id}/figures/{figure_id}/reanalyze`（拡張）

`backend/api/routes/figure_presentation.py`

```python
class FigureReanalyzeRequest(BaseModel):
    hint_text: str | None = None          # 教員の言葉の指示（≤2000字）
    focus_bbox: list[float] | None = None # [x0,y0,x1,y1] 画像内相対 0..1
```

- **body なし / 両フィールド null = 従来動作**（後方互換。既存ボタンはそのまま動く）
- 検証（422）: `hint_text` は strip 後 1〜2000 字（空白のみは null 扱い）。
  `focus_bbox` は長さ4・各値 0..1・`x1>x0`・`y1>y0`。極小領域
  （幅または高さ < 0.02）は 422「領域が小さすぎます」。
- 権限・コスト・エラーマッピングは既存のまま（`_ensure_document_editable` /
  CostGate / `{"not_found":404, "limit":429, その他:422}`）。
- **レスポンス拡張**: 既存フィールドに加え
  `"guidance": {"hint_text": ..., "focus_bbox": ...} | null` と
  `"guidance_note": str`（LLM の指示への応答。指示なし時は空文字）を返す。
- **監査拡張**: 既存 `record_review_event(...)` の payload に
  `"guidance": {"hint_text": <全文>, "focus_bbox": [...]}` を追加（指示なし時は載せない）。

### 4-2. `PATCH .../presentation-mode` は変更なし

## 5. core 変更（`backend/core/figure_reanalysis.py`）

```python
def reanalyze_figure(
    document_id, figure_id, *, created_by,
    guidance: dict | None = None,   # {"hint_text": str|None, "focus_bbox": list|None}
    agent=None, storage=None, enforce_cost_gate=True,
) -> dict:
```

追加処理（すべて既存フローへの挿入。コスト計上位置＝「有償 vision 境界の直前」は不変）:

1. `_normalize_guidance(guidance)` — ルート層と同じ検証をもう一度行う正本
   （core を直接呼ぶ経路・テストでも安全に）。不正は `FigureReanalysisError(kind="invalid")`。
2. `_crop_focus_image(image_bytes, focus_bbox) -> bytes | None` —
   PyMuPDF（`fitz.open(stream=..., filetype=...)` → `page.get_pixmap(clip=...)`）で
   相対 bbox をピクセル矩形に変換してクロップ PNG を生成。**拡大率はクロップの
   自然サイズのまま**（リサイズしない。トークン計上は既存の vision 推計に従う）。
   失敗時は None（クロップなしで続行、hint_text だけでも意味がある。fail-soft）。
3. `_labels_in_focus(inner_labels, figure_bbox, focus_bbox) -> list[str]` —
   focus をページ座標へ変換し、`inner_labels[].bbox` と**交差**するラベル text を列挙
   （中心点包含ではなく交差判定。ラベルが領域境界にまたがるケースを拾う）。
   `figure_bbox` が無ければ空リスト（§3）。
4. `FigureImageInput` に guidance フィールドを設定（§6）。
5. annotation body / 戻り値に guidance と `guidance_note` を反映:
   `normalize_figure_analysis_candidate` へ渡す dict に手を入れず、
   `deliberation_store.create_annotation(...)` の `body` へ
   `"guidance": {...}` を追加キーとして同乗させる（body は JSONB・追加キーは互換）。
   `reason` は従来の `mode_reason` 系に加え、guidance ありなら先頭に
   `「教員指示付き再解析」` を付す（GF5、レビュー時の出所明示）。

**migration は不要**。指示の保存先は①監査イベント（永続・全文）
②候補 annotation の body（レビュー文脈で参照）で足りる。教材テーブルに列を足さない。

## 6. agent 変更（`src/episteme_graph/agents/apparatus_semantics/`）

L層が所有する agent への**追加のみ・後方互換**の拡張（GF7）。

### 6-1. `schema.py`

```python
@dataclass
class FigureImageInput:
    ...（既存フィールド不変）
    # 教員指示付き再解析（deliberation 経由のみ。バッチパイプラインでは常に既定値）
    guidance_text: str = ""              # 教員の言葉の指示（正規化済み）
    focus_bbox_rel: list | None = None   # [x0,y0,x1,y1] 画像内相対 0..1
    focus_image_bytes: bytes | None = None  # クロップ済み拡大画像（core で生成済み）
    focus_label_texts: list[str] = field(default_factory=list)  # focus内 inner_labels
```

```python
@dataclass
class ApparatusRecord:
    ...（既存フィールド不変）
    # 教員指示への応答（LLM出力）。指示なし実行では常に空文字。
    # 指示された要素が見つからない場合も「見つからなかった」事実をここに書く（GF3）。
    guidance_note: str = ""
```

`_record_from_dict` は `guidance_note=d.get("guidance_note", "")` で旧 artifact と round-trip。

### 6-2. `input_builder.py`

- `build_image_payloads(figure) -> list[dict]`（新設・複数形）: 第1画像=原図、
  `focus_image_bytes` があれば第2画像=クロップ。既存 `build_image_payload` は
  後方互換のため残し、内部で新関数の先頭要素を返す。
- `build_guidance(figure) -> dict` : `{"hint_text", "focus_bbox_rel", "focus_label_texts",
  "has_focus_image"}` を正規化して返す（hint は 2000 字で再キャップ）。

### 6-3. `prompt.py`

guidance があるときのみ user content に追加:

```
## Teacher Guidance (attention directive from a human reviewer)
hint: <hint_text>
focus_region (relative coords in the first image): [x0, y0, x1, y1]
The second attached image is a magnified crop of this focus region.
In-figure labels inside the focus region: <focus_label_texts / "unavailable">

Rules for using this guidance:
- The guidance directs your attention; it is NOT evidence. evidence_quote
  fields must still be verbatim substrings of the caption/nearby text only —
  never quote the teacher's hint as evidence.
- Prioritize detecting components inside the focus region, but do not delete
  or contradict correct findings outside it.
- In the output field "guidance_note", state briefly how you applied the
  guidance. If you could not find what the teacher pointed at, say so
  explicitly — do not fabricate a detection to satisfy the hint.
```

`_OUTPUT_SCHEMA` に `"guidance_note": "how the teacher guidance was applied, or why the
requested element could not be found; empty string when no guidance was given"` を追加。
repair メッセージ経路（`build_repair_messages`）も同じ user content を使うため自動で追従。

### 6-4. `agent.py`

- `image_payloads = self._input_builder.build_image_payloads(figure)` に差し替え、
  `self._llm_client.generate(messages, images=image_payloads)`（llm_client は既に
  list 対応済み・変更不要）。
- それ以外のフロー（validator → repair → `_attach_label_grounding` →
  `_attach_profile_grounding`）は不変。**guidance は grounding に一切関与しない**（GF4）。

### 6-5. `repair.py` / `validator.py`

- `_parse_record`: `guidance_note` をパース（str 化・2000字キャップ）。
- validator 追加ルール（いずれも **warning**・error にしない）:
  - `guidance_note_missing`: guidance 入力があるのに `guidance_note` が空
  - `guidance_note_unexpected`: guidance 入力がないのに `guidance_note` が非空
    （プロンプト混入の検知）
- 既存の hard error（`label_ref` の inner_labels 実在検査・`source_backed` 禁止・
  evidence_quote 逐語検査）はそのまま guided 実行にも効く。

## 7. フロントエンド（`frontend/public/js/deliberation.js` + `deliberation.css`）

ES5・`window.Deliberation` の既存規約に従う。

### 7-1. UI 追加（`_figureModeHtml` のモードレビュー行の直下）

```
[□ 領域を指定して再解析]  ← トグルボタン #deliberation-focus-toggle
  （ON中は原図ステージがドラッグ描画モード。描画済みなら「領域をクリア」ボタン表示）
[AIへの指示（任意・2000字まで）                    ] ← textarea #deliberation-reanalyze-hint
  placeholder: 例「左下の EOM と書かれた箱が変調器。3.2節の説明に対応する」
[AIで図を再解析]（既存ボタン。guidance があれば body に載せて送る）
```

### 7-2. 矩形ドラッグ描画

- 描画レイヤーは既存 `#deliberation-figure-overlays` の**兄弟**として
  `#deliberation-figure-focus-layer` を追加（既存オーバーレイのクリック選択と干渉させない。
  focus モード ON のときのみ `pointer-events: auto`）。
- mousedown → mousemove → mouseup で矩形を描き、画像要素の実表示サイズに対する
  相対座標 0..1 に正規化して保持（`figureImageState.focusBbox`）。touch 系イベントも
  同じハンドラに束ねる（タブレットでのレビューを想定）。
- 描画済み矩形は点線枠 + 半透明塗りで常時表示。「領域をクリア」で削除。
- モーダル再描画（`_reloadOverview`）・図切替時に focus 状態をリセット
  （`_resetFigureImageState` に同乗）。

### 7-3. 送信・結果表示

- `_bindFigureReanalysis` を拡張: `hint_text`（strip・空なら送らない）と
  `focusBbox` から body を組み立て、両方空なら**従来どおり body なし**で POST。
- 成功時 status 文言: `guidance_note` が返れば
  `「構造化候補を作成しました。AIの応答: <guidance_note 先頭120字>」`、
  なければ既存文言のまま。指示した要素が見つからなかった場合、教員はここで即座に知れる（GF3）。
- 送信した guidance は成功・失敗にかかわらず**消さない**（再試行時に微修正して再送できる）。

## 8. テスト計画

### `src/tests/agents/apparatus_semantics/`（agents 用）

- `test_input_builder.py` 追加: `build_image_payloads` が focus あり=2枚/なし=1枚、
  `build_guidance` の正規化・キャップ
- `test_prompt_guidance.py`（新設）: guidance セクションの有無切替、
  「hint を evidence として引用禁止」文言の存在（GF2 の構造的検査）、
  focus_label_texts unavailable 表記
- `test_schema.py` 追加: `guidance_note` round-trip、旧 dict（フィールド欠落）からの復元
- `test_validator.py` 追加: `guidance_note_missing` / `guidance_note_unexpected` が
  warning であること（error にしない）
- `test_agent.py` 追加: fake llm_client に渡る images が2枚になること、
  guided 実行でも `review_status` が `review_required` のままであること（GF1）

### `backend/tests/`

- `test_figure_presentation_api.py` 追加: body なし後方互換 / 不正 bbox・過長 hint の 422 /
  guidance がレスポンスと監査 payload に載ること
- `test_figure_reanalysis.py` 追加: `_normalize_guidance` 境界値、
  `_labels_in_focus` の交差判定（figure_bbox NULL で空）、
  `_crop_focus_image` 失敗時の fail-soft 続行、
  fake agent への `FigureImageInput.guidance_*` 伝搬、
  annotation body への guidance 記録、CostGate が guided でも同一キーで効くこと（GF6）
- `test_deliberation_ui_static.py` 追加: `#deliberation-focus-toggle` /
  `#deliberation-reanalyze-hint` / focus レイヤーの静的存在検査
- ガードレール（`test_image_library_guardrails.py` へ追加）:
  guided 経路でも `source_backed` 自動付与なし・確定 API 迂回なし（GF1/GF4）、
  orchestrator が guidance フィールドを一切設定しないこと（GF7）

## 9. 非スコープ（Phase 2 候補・別 issue）

- **周辺本文ブロックの明示選択**: `collect_figure_context` の候補ブロックを UI に列挙し
  「この段落と結びつく」をチェック → `context_block_ids` として nearby_text へ優先注入
  （自動ヒューリスティックの手動上書き）
- **label↔説明文リンクの永続化**: 教員確定した対応を annotation（kind='link'）として保存し、
  次回のバッチ再解析・figure_context 収集でも尊重する
- **複数 focus 領域**（v1 は1矩形のみ）
- **バッチパイプラインへの guidance 適用**（v1 は deliberation の単図再解析のみ）
- 学習者向け表示への guidance 露出（従来どおり教員レビュー領域に閉じる）

## 10. 実装順序

1. schema.py / input_builder.py / prompt.py / repair.py / validator.py / agent.py（agents 側、単体テストと同時）
2. figure_reanalysis.py（クロップ・focus ラベル・guidance 伝搬）
3. figure_presentation.py（API body・監査）
4. deliberation.js / CSS（描画 UI・送信・guidance_note 表示）
5. ガードレール・静的 UI テスト
