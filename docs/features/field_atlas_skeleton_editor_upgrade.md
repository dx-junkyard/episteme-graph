# 分野の地図 — 骨格エディタ改善（AIアシスト編集 + ビジュアルプレビュー）

> **ステータス: 実装済み（P1〜P5）— 正本・凍結**
> （起票時のブランチ名 `learning-ux` は現在の `ura-dev` にマージ済み。2026-09-03 コード照合で
> `atlas_generator.{interpret_skeleton_instruction, propose_skeleton_edit, apply_json_patch}` と
> `POST /api/admin/cartridges/{id}/atlas/skeleton/assist/{interpret,propose}`・
> `ATLAS_ASSIST_MAX_CALLS_PER_DAY`・`atlas-draft-preview.js` / `atlas-assist-panel.js` の
> 現存を確認した。`apply_json_patch` はその後、カテゴリギャップ候補・辺候補の
> 「下書きへ反映」でも再利用されている）
> 関連: `field_atlas_skeleton.md` / `field_atlas_db_managed_skeleton.md` / `field_atlas_overlay_spec.md`
>
> 注記 (2026-08-14): `field_atlas_overlay_spec.md` の原本は消失している。現存するのは
> 2026-08-14 の**再構成版**で、**旧§番号との対応は保証されない**。
>
> 実装場所: `backend/core/atlas.py`（`ValidationIssue`）/
> `backend/core/atlas_generator.py`（`interpret_skeleton_instruction` /
> `propose_skeleton_edit` / `apply_json_patch`）/ `backend/api/routes/atlas.py`
> （`assist/interpret` / `assist/propose`）/ `frontend/public/js/atlas-draft-preview.js`
> / `frontend/public/js/atlas-assist-panel.js` / `admin.js` の `initAtlas()`。
> §7 未決事項の決定: (1) JSON Patch (RFC 6902) を採用 / (2) コスト上限は
> `ATLAS_ASSIST_MAX_CALLS_PER_DAY`（既定 60・当日 assist 呼び出し数で判定）/
> (4) 会話履歴はブラウザ内メモリのみ（draft 保存・再生成・凍結で無効化）。

## 1. 課題

管理画面「分野の地図」の骨格レビューは、`atlas_skeleton` の JSON を
生の `<textarea>` に丸ごとダンプして手編集させる UI になっている
（`frontend/public/js/admin.js` `initAtlas()` 内 `renderState()`）。

- 教員は `regions[].concepts[].layout.{x,y}` のような正規化座標を
  数値のまま調整しており、実際の配置結果を確認する手段がない
  （保存 → 学習者向けオーバーレイを開く、以外に見る方法がない）
- エラーは `setStatus()` による赤字1行のみで、JSON構文エラーは
  そもそも `validate_skeleton()`（`backend/core/atlas.py:422`）まで
  到達しない（クライアント側 `JSON.parse` が先に失敗して終わる）
- `validate_skeleton()` はフィールド単位の詳細なエラー
  （id重複・座標範囲外・エッジ参照切れ・領域重なり等）を返しているが、
  UI は `renderValidation()` で平文リストとして表示するだけで、
  該当箇所がテキスト中のどこかを教員が目視で探す必要がある
- 骨格生成は `POST .../atlas/skeleton/generate` の全体再生成のみで、
  「この領域だけ直して」「この概念のラベルをもっと分野の用語に合わせて」
  のような部分修正を AI に頼む手段がない

これらは JSON エディタとしての限界であり、AIアシスト編集と
ビジュアルプレビューの追加は妥当な解決の方向だと判断する。

## 2. 設計方針

- **A層（生成パイプライン）・DB スキーマ・`validate_skeleton()` の
  検証ルールは変更しない。** 本改善は admin UI と、それを補助する
  新規 API（部分編集アシスト）に閉じる。
- 学習者向けオーバーレイ（`atlas-overlay.js`）が既に「正規化座標
  → SVG 描画」の視覚言語（`C.verifiedFill` / 破線 / 霧ハッチ等）を
  持っているため、**教員用プレビューはこれを再利用**し、
  新しい視覚言語を作らない（教員が「学習者にはこう見える」を
  そのまま確認できることが目的）。
- draft は凍結前の教員専用データなので、プレビューは
  `seed_status.reviewed=false` の状態も含めて**すべて可視化してよい**
  （学習者向け描画が課す `display_seed_status` の隠蔽ルールは
  プレビューには適用しない — draft は元々学習者非公開のため）。

## 3. 機能A: ビジュアルプレビュー

### 3.1 UI

骨格レビューセクションに「JSON編集」「プレビュー」のタブ切り替えを追加する
（既存の `atlas-draft-editor` textarea はそのまま残す。置き換えない）。

- プレビューは `atlas-overlay.js` の SVG 描画ロジック（region 矩形・
  concept 円・edge 破線・ラベル）を流用した読み取り専用コンポーネント
  `atlas-draft-preview.js` として新規実装する
- 学習者向けと区別するため、seed_status 未レビューの概念にも
  色を付ける（学習者向けでは非表示の情報を教員には見せる）。
  画面上部に「これは draft のプレビューです。学習者には表示されません」
  の注記を出す
- 領域の重なり（`validate_skeleton` の warning）はプレビュー上で
  赤枠ハイライトし、クリックで該当 warning テキストを表示する
- JSON編集タブでの編集内容は、フォーカスを外した時点（`blur`）で
  `JSON.parse` を試み、成功すればプレビューに即時反映する
  （保存前のライブプレビュー。サーバー往復は発生させない）

### 3.2 座標変換

`atlas-overlay.js` の `renderL1()` は「正規化 0-1 座標 → viewBox 座標」
の変換が呼び出し元（サーバー側 `atlas_state` / `atlas-data.js`）で
済んだデータを受け取る前提になっている。draft プレビューでは
この変換ステップが無いため、`atlas-draft-preview.js` に
「`region.layout.{x,y,w,h}` (0-1) → viewBox 座標」の薄い変換関数を
新規実装する（サーバーを経由しない、クライアント内で完結する計算）。

### 3.3 検証エラーのインライン表示

`renderValidation()` が受け取る `ValidationReport.errors/warnings` の
メッセージは `"領域 'foo' の layout 座標が..."` のように対象IDを
含む自然文である。文字列パースで対象 region/concept id を抽出するのは
壊れやすいため、**サーバー側の `ValidationReport` にフィールドを追加**する:

```python
# backend/core/atlas.py — ValidationReport 拡張案
@dataclass(frozen=True)
class ValidationIssue:
    message: str
    region_id: str | None = None
    concept_id: str | None = None
    edge: tuple[str, str] | None = None

@dataclass(frozen=True)
class ValidationReport:
    errors: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()
```

既存の `errors: tuple[str, ...]` 呼び出し元（`freeze` エンドポイント等）
との互換のため、`ValidationIssue.__str__` が既存の文言を返すようにし、
`PUT .../atlas/skeleton/draft` のレスポンス JSON にのみ
`region_id`/`concept_id` を追加フィールドとして載せる
（後方互換：既存クライアントは無視すればよい）。

プレビュー側はこの id を使って該当ノードに赤い枠を描く。

## 4. 機能B: AIアシスト編集（対話形式）

現状 `POST .../atlas/skeleton/generate` は骨格全体を1回のLLM呼び出しで
作り直すのみ（`atlas_generator.generate_skeleton_draft()`）。
これに加えて、**draft の一部分だけを対象にした対話型の編集アシスト**を
新設する。1回の自由文入力から即座にパッチを生成するのではなく、
**「何を・どう変えたいか」をAIが言い直して教員に確認させる意図解釈の
ステップを、実際の修正提案の前に必ず挟む**。

### 4.1 UI: AI agent ボタン

- 骨格レビュー画面（プレビュー/JSON編集タブの外側）に常設の
  **「AI agent」ボタン**を配置する。押すと画面右または下部に
  チャット形式のパネル（`atlas-assist-panel.js`、新規）が開く
- パネルはドラフト単位（`cartridge_id` + `draftRevision`）に紐づく
  会話履歴を保持する。draft を保存・再生成・凍結すると
  「ドラフトが更新されました。会話を続けますか？」と促し、
  古い revision に基づく提案は無効化する（4.3 の楽観ロックと連動）
- プレビュー上のノード（領域・概念）をクリックしてからチャットに
  発言すると、そのノードが「今回の発言候補スコープ」としてUIに
  ピル表示される。ただし**これは AI への強制ではなくヒント**であり、
  最終的な対象特定は 4.2 の意図解釈ステップが行う（クリックした
  ノードと発言内容が矛盾する場合、AI はどちらを指しているか問い返す）
- 会話履歴はブラウザ内メモリのみで良い（MVP）。ページリロードや
  タブを閉じると消える。永続化するかは §7 未決事項

### 4.2 意図解釈ステップ（修正の前に必ず挟む）

ユーザーの発言をそのまま編集指示として実行しない。まず
**「対象・要望・会話の連続性・望む状態変化」を解釈し、教員に
言い直して見せて確認を取る**ステップを独立の API 呼び出しとして持つ。

```
POST /admin/cartridges/{cartridge_id}/atlas/skeleton/assist/interpret
```

```jsonc
// request
{
  "revision": 12,
  "message": "さっきの続きだけど、そっちの円もお願い",
  "history": [
    { "role": "teacher", "content": "この領域の概念ラベルを教科書的な用語に揃えて",
      "resolved_target": { "region_id": "foundations_and_motivation" } },
    { "role": "agent", "content": "『重力の修正』→『修正重力理論の動機』で提案しました" }
  ],
  "selection_hint": { "concept_id": "linearized_field" }  // 直前にクリックしたノード（あれば）
}
```

```jsonc
// response — まだ何も変更しない。解釈結果を人間可読な形で返すだけ
{
  "interpretation": {
    "is_continuation": true,          // 直前のやりとりの続きと判断
    "continuation_of": "turn_2",      // history 中のどの発言を継続対象と見たか
    "target": {
      "kind": "concept",              // region | concept | edge | region_set | unresolved
      "concept_id": "linearized_field",
      "label_snapshot": "線形化された場"
    },
    "requested_change": "concept 'linearized_field' のラベルも教科書的な用語に揃える",
    "ambiguous": false,
    "clarifying_question": null,
    "confidence": 0.78
  }
}
```

- `ambiguous: true` の場合、`patch` はまだ生成せず
  `clarifying_question`（例:「『そっちの円』とは "線形化された場" の
  ことで合っていますか？それとも別の概念ですか？」）をチャットに
  そのまま表示し、教員の返答を次のターンの `message` として送る
  ループにする。**解釈が曖昧なまま次の提案ステップに進めない**
- `is_continuation` の判定は、直近 `history` の対象（region/concept/edge）
  と今回の発言の指示語（「さっきの」「そっちの」「同様に」等）・
  スコープ言及の有無から LLM に判断させる。新しい話だと判断した場合は
  `is_continuation: false` とし、`selection_hint` や skeleton 全体を
  対象文脈として再走査する
- この解釈結果はチャットパネルに**AIの発言としてそのまま表示**し、
  教員は「合っています」ボタン、または直接テキストで訂正
  （例:「違う、隣の領域の方」）して返せる。訂正した場合は
  訂正メッセージを新しい `message` として再度 `interpret` を呼ぶ
  （教員が明示的に確定するまで 4.3 の提案生成ステップに進まない）

### 4.3 差分提案ステップ

教員が意図解釈の内容を確認・確定した後にのみ、実際の編集案を生成する。

```
POST /admin/cartridges/{cartridge_id}/atlas/skeleton/assist/propose
```

```jsonc
// request — 4.2 で確定した interpretation をそのまま渡す（再解釈しない）
{
  "revision": 12,
  "confirmed_interpretation": { /* 4.2 の interpretation オブジェクト */ }
}
```

```jsonc
// response — 上書きではなく「差分案」を返す（教員が個別に承認する）
{
  "revision": 12,
  "proposal": {
    "summary": "概念 'linearized_field' のラベルを更新",
    "patch": [
      { "op": "replace", "path": "/regions/0/concepts/2/label",
        "before": "線形化された場", "after": "線形化された重力場" }
      // JSON Patch (RFC 6902) 形式
    ]
  },
  "warnings": []
}
```

- `revision` の突合は既存の draft PUT と同じ楽観ロックを流用する
  （assist はドラフトを直接書き換えない。**提案のみ**返す）
- 教員は差分（before/after）をプレビュー上でハイライト表示された
  状態で確認し、「適用」ボタンで初めて `PUT .../atlas/skeleton/draft`
  が呼ばれる。**AIが直接draftを確定させない**（既存の
  「LLMが確定情報を書かない」原則 — `seed_status` の `reviewed=False`
  強制と同じ思想を踏襲）
- LLM 呼び出しは `atlas_generator.py` に
  `interpret_skeleton_instruction(skeleton, message, history, selection_hint) -> Interpretation`
  と `propose_skeleton_edit(skeleton, interpretation) -> EditProposal`
  の2関数として追加する（解釈と提案で prompt を分離し、
  「対象特定」と「編集内容生成」の責務を混ぜない）
- 出力は `validate_skeleton()` に通してから返す。patch 適用後に
  検証エラーが出る提案は「警告つきで提示」し、教員が承認しても
  `PUT` 側の 422 で最終的に弾かれる（二重チェック）

### 4.4 チャットUIの表示

- チャットパネルは教員発言・AI発言を時系列表示する通常のチャットUI
- AI発言には種類が2つある: **(a) 意図確認カード**
  （対象ノードのラベル・要望の言い直し・「合っていますか？」ボタン）と
  **(b) 差分提案カード**（before/after diff・「適用」「破棄」ボタン）。
  (a) を確認しないと (b) は出てこない
- 意図確認カード上の対象ノードはプレビュー上でも同時にハイライトする
  （チャットとプレビューを常に連動させ、教員がテキストだけで
  対象を誤認しないようにする）
- 「別の話です」ボタンを意図確認カードに用意し、押すと
  `history` をリセットして新しい会話として次の発言を送れるようにする
  （`is_continuation` の自動判定に対する明示的な訂正手段）

## 5. 段階的実装案

| 段階 | 内容 |
|---|---|
| P1 | `atlas-draft-preview.js` 新設。JSON編集タブと並列表示のみ（読み取り専用、AIなし） |
| P2 | `ValidationReport` に `region_id`/`concept_id` を追加し、プレビュー上にエラー/警告をインラインハイライト |
| P3 | 「AI agent」ボタン + チャットパネル新設。`POST .../assist/interpret`（意図解釈のみ、まだ編集しない）を実装し、対象・要望・継続判定をチャットで確認できるようにする |
| P4 | `POST .../assist/propose`（4.2 で確定した interpretation から差分案生成、JSON Patch形式）。diff 承認フローと `PUT .../atlas/skeleton/draft` への適用を追加 |
| P5（任意） | ノードクリック→スコープ候補ヒント（4.1）、「別の話です」による会話リセット等の UX 磨き込み |

## 6. 影響範囲外（変更しないもの）

- `backend/core/atlas.py` の `parse_skeleton()` / `validate_skeleton()` の
  検証ルール本体（`ValidationReport` のデータ構造のみ拡張）
- `atlas_generator.generate_skeleton_draft()`（全体生成フロー）
- 学習者向け `atlas-overlay.js` / `atlas-view.py` の描画・配信ロジック
- 凍結・楽観ロック・changelog の既存フロー（`freeze` エンドポイント）

## 7. 未決事項

1. JSON Patch (RFC 6902) 形式を採用するか、もっと単純な
   `{region_id, concept_id, field, value}` 形式にするか
2. assist（interpret/propose 双方）のコスト上限（tension/structure_anchor
   と同様に セッション/日次の呼び出し回数上限を設けるか）
3. スコープを跨いだ提案（例: 概念を別領域に移動）を P4 で許容するか、
   P5 以降に回すか
4. 会話履歴（4.1）をブラウザ内メモリのみに留めるか、
   `course_builder_sessions`（A1）と同様に draft 単位で
   DB永続化するか。永続化する場合、凍結・draft再生成時の
   履歴の扱い（破棄するか、旧revisionの会話として残すか）を決める
5. `is_continuation` の自動判定精度が低い場合のフォールバック
   （常に確認カードを出す above、誤判定時のコストをどこまで許容するか）
