# 要素インベントリ（Element Inventory / 検出要素の一覧）設計

> **状態: 実装着手（2026-07-17。§13 の未決 1〜3 はオーナー決定済み: 1=クライアントサイド
> フィルタで確定 / 2=図・画像ボタンと並置で確定 / 3=原稿スタジオ側にも入口を置く（v1 スコープ入り、
> §9-3））**。本書が本機能の設計の正本。
> 親ドキュメントは `element_deliberation_workspace_design.md`（W層）。本機能は W層の
> **教材単位の統合入口**であり、新しい層ではない（W層の導線拡張 + 読み取り専用 API 1本）。

## §1 背景・目的

W層「深く検討」の導線は現在、**要素が既に表示されている画面に文脈的（contextual）に
埋め込まれている**:

| 要素型 | 現在の導線 |
|---|---|
| figure | 教材管理 → 図・画像モーダル |
| equation | 教材管理 → 反復改善（revisions）モーダルの変更差分 |
| theory_component | 原稿スタジオ → 論理要素ビュー・選択中コンポーネント |
| theory_claim | 原稿スタジオ → チャンク主張ビュー |

この設計は「見ているものをその場で掘る」には良いが、次の欲求に応えられない:

- **俯瞰**: 「この教材からパイプラインが何を検出したか」を1画面で見渡したい
- **探索**: 種別・キーワードで絞り込んで目当ての要素に到達したい
  （例:「このコンポーネントに関する claim を全部見たい」「“dispersion” を含む数式は？」）
- **検討カバレッジ**: どの要素が既に検討済み（注釈・対話・同一性リンクあり）で、
  どれが手つかずかを知りたい

とりわけ equation は revisions 差分に現れたものにしか導線が無く、
theory_component / theory_claim は原稿スタジオ（コース文脈）を経由しないと開けない。
**教材管理から、その教材の全検出要素へ直接到達できる第5の導線**を追加する。

## §2 スコープ・位置づけ

- 教材管理タブの各教材行に「**検出要素**」ボタンを追加（「図・画像」の隣。
  `document_id` がある教材のみ活性 — 図・画像ボタンと同条件）。
- 押すと**要素インベントリモーダル**が開き、当該 document の
  `theory_component / theory_claim / equation / figure` 全件を統一カードのリストで表示。
- 種別チップ + キーワード入力でクライアントサイドフィルタ。
- 各カードの「深く検討」が既存 `window.Deliberation.openElement(type, id, {documentId, title})`
  を呼ぶ（W層モーダルは**非改変**でそのまま使う）。
- 既存の4導線は**すべて残す**（文脈的導線と俯瞰導線は補完関係）。
- 図・画像モーダルも**残す**。サムネイル・装置候補・bbox オーバーレイ・ライブラリ昇格は
  図モーダルの専門領分であり、インベントリはそれを複製しない（figure カードは
  テキスト情報 + 深く検討のみ。視覚作業は図モーダルへ）。

**名称・接頭辞**: コード・API・UI 文言は W層の規約どおり `deliberation-` / `element-`
プレフィックスを使う（Field Atlas / Assumption Atlas と衝突させない）。
UI 表示名は「検出要素」（ボタン）/「検出要素の一覧」（モーダル見出し）。

## §3 不変条項（親 W層から継承 + 本機能固有）

- **I1 読み取り専用・非LLM・同期**: インベントリ構築は既存データの読み出しと整形のみ。
  LLM 呼び出しゼロ・DB 書き込みゼロ・監査記録なし（閲覧系）。
- **I2 fail-closed（W5）**: API は `_require_teacher` + `_ensure_document_viewable`。
  全要素が同一 document に属するため per-element ゲートは不要（これがこの API 形状を
  選ぶ理由でもある — 横断一覧にすると per-element 権限が要る）。
- **I3 数値を見せない（W8）**: LLM 由来の confidence 生数値をカードに含めない
  （equations.json の `confidence` は SELECT/整形段階で落とす）。件数バッジ
  （注釈 n 件等）は教員向け管理情報であり可（原稿スタジオの「主張: N / 未レビュー: M」と同格）。
- **I4 正直な省略**: 型別上限（§5）で切り詰めた場合は `truncated_types` で明示する。
  黙って欠落させない。
- **I5 情報を落とさない（P4）**: review_required / candidate / failed 等の状態はカードに
  そのまま出す（隠さない・確定表示しない）。図の `match_status` は既存の
  「（要確認）」注記と同じ段階表示に従う。
- **I6 A層非改変**: `src/episteme_graph/agents/` と既存 W層 core（refs / decomposition /
  positioning / dialogue / annotations）は変更しない。追加は `inventory.py` 1ファイル +
  route 1本 + フロント。

## §4 データソースと統一カード（ElementCard）

4型それぞれの列挙元。すべて既存のクエリパターン・関数を再利用する:

| 要素型 | 列挙元 | 既存の参照実装 |
|---|---|---|
| theory_component | `theory_components WHERE source_scope->>'document_id' = :doc` | `core/deliberation/decomposition.py`（apparatus 候補列挙）・`routes/theory_components.py::_select_components_sql` |
| theory_claim | `theory_claims WHERE document_id = :doc`（`idx_theory_claims_document` あり） | migration 013 |
| equation | `refs.equation_records(document_id)`（最新 run の `stage_outputs._artifacts.equation_semantics.equations`） | `core/deliberation/refs.py`（既存関数をそのまま呼ぶ） |
| figure | `document_figures WHERE document_id = :doc` | `routes/admin.py` figures API |

**ElementCard（統一スキーマ）**:

```jsonc
{
  "element_type": "theory_claim",        // 4語彙のいずれか
  "element_id": "<uuid | equation_id>",  // openElement にそのまま渡せる id
  "label": "…",                          // 一覧の表示名（下表）
  "snippet": "…",                        // 本文抜粋（キーワードフィルタの対象。最大~300字で切る）
  "badges": { … },                       // 型ごとの状態語彙（下表。確定表示しない）
  "location": { … },                     // 出所（chunk_id / section_id / page 等）
  "deliberation": {                      // 検討状況の要約（§7）
    "annotations": {"candidate": 0, "committed": 0, "dismissed": 0},
    "sessions": 0,
    "identity_links": {"candidate": 0, "confirmed": 0, "rejected": 0}
  }
}
```

型ごとのフィールド対応:

| 型 | label | snippet | badges | location |
|---|---|---|---|---|
| theory_component | `name` | `summary` | `component_type` / `status` / `review_status` | `source_scope` 全体（section_id 等を含む superset。P4） |
| theory_claim | `text` 先頭 ~80字 | `text`（+`evidence_text` 先頭） | `claim_type` / `support_status` / `review_status` | `chunk_id` |
| equation | `label` あるいは `equation_id` | `plain_text`（無ければ `latex` 文字列。レンダリングしない） | `equation_type` / `semantic_status` / `extraction_status` | `source_location`（page 等） |
| figure | `figure_label` あるいは `figure_key` | `caption_text` | `extraction_method` / `status`（`extracted`/`failed`。※`match_status` は `document_figures` の列ではなく装置候補側の情報のため v1 カードに含めない — 実装時補正 2026-07-17） | `page` |

並び順: 型グループ順（component → claim → equation → figure）、型内は各ソースの自然順
（component: `created_at`、claim: chunk 出現順（`chunk_id` 経由の `chunk_index`、
取れなければ `created_at`）、equation: artifact 配列順、figure: `page` 順）。
型横断の統一文書順ソートは v1 ではやらない（§12）。

## §5 API

```
GET /api/admin/deliberation/documents/{document_id}/elements
```

- 配置は既存 `routes/deliberation.py`（`prefix="/deliberation"`、main.py 登録は既存のまま）。
- ゲート: `_require_teacher` + document 閲覧判定。
- document_id 解決: route は `services.resolve_document_access(user_id, document_id)` で
  **正規化 + 閲覧判定を1回で**行う（`documents.id`(UUID) と `source_path`(material_id) の
  両対応）。不在・非閲覧は 404（存在の秘匿は既存 `_ensure_document_viewable` の挙動に合わせる）。
  core `build()` へは **正規化済み `access.document_id`** を渡す（原稿スタジオは
  `sources[].material_id` を渡してくるため、ここで正規化しないと各テーブルの
  `document_id` 照合が空振りする）。
- クエリパラメータ: **v1 では無し**（フィルタは全面的にクライアントサイド。§6）。
- レスポンス:

```jsonc
{
  "document_id": "…",
  "elements": [ElementCard, …],
  "counts": {"theory_component": 12, "theory_claim": 87, "equation": 23, "figure": 6},
  "truncated_types": []            // 型別上限 500 を超えて切った型名（I4）
}
```

- 型別上限 `_INVENTORY_MAX_PER_TYPE = 500`（モジュール定数。1論文の現実的規模では
  到達しない安全弁。到達時は `truncated_types` で正直に返す）。
- run が無い / artifacts が無い document でも 200（equation が 0 件になるだけ。
  他の型は DB 行があれば出る）。404 は document 不在・閲覧不可のみ。

## §6 フィルタ設計 — v1 はクライアントサイド

判断: **サーバは常に全件（上限付き）を返し、種別チップ・キーワードはフロントで即時フィルタ**。

理由:
1. equation は JSON artifact 由来でどのみち Python/JS 内フィルタになる。SQL 側と二重に
   フィルタ実装を持たない（分割ロジックの二重実装を禁じた lecture の教訓と同型）。
2. 1教材の要素数は高々数百件・テキスト主体で数百KB未満。1回のフェッチで足りる。
3. キー入力ごとの再フェッチ（実質ポーリング）を構造的に排除できる（家風）。
4. 上限到達時のキーワード検索漏れは `truncated_types` の明示 + モーダル内の事実文
   （「◯◯は500件で省略されています」）で正直に伝える（I4）。

キーワードマッチ対象は `label + snippet`（小文字化した部分一致。正規化・形態素解析は
やらない）。種別チップは `すべて / 論理要素 / 主張 / 数式 / 図・画像` の5つ + 件数併記。

## §7 検討状況の集約（deliberation フィールド）

「どの要素が手つかずか」を見せるため、W層の活動テーブル3枚を document 単位で
GROUP BY 集計し、`(element_type, element_id)` でカードに合流する:

```sql
SELECT element_type, element_id, status, COUNT(*) FROM element_annotations
 WHERE document_id = :doc GROUP BY 1, 2, 3;
SELECT element_type, element_id, COUNT(*) FROM deliberation_sessions
 WHERE document_id = :doc GROUP BY 1, 2;
SELECT instance_element_type, instance_element_id, status, COUNT(*) FROM element_identity_links
 WHERE instance_document_id = :doc GROUP BY 1, 2, 3;
```

- いずれも document_id 系インデックスが既にある（migration 048/049）。3クエリ固定で
  N+1 なし。
- UI では「検討済み」バッジ（committed/confirmed が 1 件以上）・「候補あり」バッジ
  （candidate が 1 件以上）程度の段階表示に留める。dismissed はカードに出さない
  （API では返す — 情報は落とさない・表示だけ抑制）。

## §8 core 実装 — `backend/core/deliberation/inventory.py`

- 新規1ファイル。**FastAPI 非 import**（既存ガードレール
  `test_deliberation_guardrails.py` の module-tree 検査が新ファイルも自動で対象にする
  ことを確認する）。
- 公開関数: `build(document_id: str) -> dict`（§5 のレスポンス形をそのまま返す。
  route は権限ゲートと整形だけ）。
- DB は `core.postgres.get_session` 直（refs / decomposition と同じ流儀、`try/finally close`）。
- equation は `refs.equation_records(document_id)` を呼ぶだけ（実装を複製しない）。
  `confidence` はカード化の段階で落とす（I3）。
- snippet 切り詰め・label 導出は純粋関数に分離し、fake rows でユニットテスト可能にする
  （personal_graph の derive と同じテスト戦略）。

## §9 フロント実装

- **`deliberation.js`**: `window.Deliberation.openInventory(documentId, {title})` を追加。
  モーダル DOM 生成・チップ/キーワードフィルタ・カード描画・「深く検討」配線を持つ。
  ES5 / IIFE / 依存は既存 `deps.apiFetch` 等をそのまま使う（追加 DI なし）。
- **`admin.js`**: 教材管理行に「検出要素」ボタンを1つ追加し
  `window.Deliberation.openInventory(m.document_id, {title: m.title||m.filename})` を呼ぶ
  **だけ**（図・画像ボタンの実装パターンを踏襲。`document_id` 無し教材は非表示 or disabled、
  図・画像ボタンと同条件）。
- **モーダル多重**: インベントリモーダルの上に既存 deliberation モーダルが重なる。
  図・画像モーダル + deliberation モーダルの既存共存パターンと同じ（deliberation モーダル
  を閉じるとフィルタ状態を保ったままインベントリに戻る。インベントリの再フェッチはしない —
  検討状況バッジの鮮度より操作の軽さを優先。手動の「再読込」ボタンを1つ置く）。
- **原稿スタジオ側の入口（§13-3 決定によりスコープ入り）**: `admin-lecture-studio.js` の
  コース構造タブ（左パネル既定タブ）のコースヘッダ（`lsRenderCourseStructure` の
  `ls-course-header`）に「検出要素」ボタンを置く。コースの sources
  （`courseStructure.sources || courseStructure.data.sources` — 既存
  `lsCoursePrimaryDocumentId()` と同じ読み方）が 1 件ならその `material_id` で
  `openInventory` を直接開き、複数件なら既存 `ls-menu` パターンの小メニューで教材を
  選ばせる。0 件ならボタンを描画しない。**material_id をそのまま渡してよい**
  （API 側が §5 のとおり正規化する）。
- **Admin Copilot アンカー（任意・G6）**: `AA.registerUiAnchors("materials", { element_inventory_button: … })`
  を追加登録すると道案内対象にできる。v1 に含めるかは実装時判断（含めない場合も
  capability registry には触れない — guidance_only の追加は後続で可能）。

## §10 権限・プライバシー

- ルートゲートは §5 のとおり。**グループ共有（object_group_permissions の
  document viewer/editor）だけで開ける**（既存の成果読み取りゲートと同格。
  「ドキュメント共有だけで全成果が閲覧可能」の現行方針に一致）。
- インベントリは同一 document 内で閉じる。他 document の情報（cross_corpus 等）は
  含めない（面②の領分。per-user フィルタが必要になる情報を持ち込まない）。
- 学習者向け表示は無い（教員専用。非スコープ §12）。

## §11 テスト・ガードレール

- `backend/tests/test_deliberation_inventory.py`（新規）:
  - core: fake rows で 4型のカード化・label/snippet 導出・並び順・上限と
    `truncated_types`・confidence 非含有（I3）・deliberation 集計の合流
  - API: 非閲覧者 404（fail-closed）・viewer で 200・run 無し document で equation 0 件
- `test_deliberation_ui_static.py` 拡張: 「検出要素」ボタンの存在・`openInventory` 公開・
  キー入力での fetch 再発行が無いこと（静的検査できる範囲で）
- 既存 `test_deliberation_guardrails.py`: core/deliberation ツリーの FastAPI 非 import
  検査が inventory.py を自動包含することを確認（していなければ対象に追加）

## §12 非スコープ（v1）

- 複数教材・コーパス横断のインベントリ（per-element 権限ゲートが必要になる。
  必要になったら W5 の `_filter_by_document_view` パターンで別途設計）
- サーバーサイド全文検索・pgvector 類似検索（意味的な近傍は面② cross_corpus の領分）
- 型横断の統一文書順ソート・ページング
- equation の LaTeX レンダリング（テキスト表示のみ。レンダリングは深く検討モーダル側の
  将来課題と合流させる）
- figure サムネイルのインベントリ内表示（認証付き遅延取得のコスト。図モーダルへ誘導）
- symbol（SymbolRegistry）・derivation step 等 **ElementRef 語彙に無い要素型の追加**
  （追加するなら W層 refs/decomposition/positioning と同時拡張が必要 — 別 issue）
- 学習者向け表示・G層 To-Do 連携・LLM による要約やランキング

## §13 未決事項（2026-07-17 オーナー決定済み）

1. ~~フィルタ方式~~ → **クライアントサイドで確定**（§6 のとおり）。
2. ~~ボタン統合~~ → **v1 は「図・画像」と並置で確定**。検討状況バッジの語彙は
   「検討済み / 候補あり / 未検討」の3段階で開始し、運用で不足すれば増やす。
3. ~~原稿スタジオ側の入口~~ → **v1 に含めることで確定**（§9-3。コース構造タブの
   コースヘッダにボタン、複数 sources は小メニューで選択）。

## §14 issue 分割

- **I-1（バックエンド）**: `core/deliberation/inventory.py` + route 1本 +
  `test_deliberation_inventory.py`。migration なし・既存コード変更は
  `routes/deliberation.py` への route 追加のみ。
- **I-2（フロント）**: `deliberation.js` に `openInventory` + `admin.js` にボタン +
  `test_deliberation_ui_static.py` 拡張。I-1 マージ後に着手。

（I-1/I-2 は小さいため1 issue・1 PR に束ねても良い。分けるなら API 契約＝§4/§5 を先に固定する。）
