# 分野マップの関係表示（辺候補レビューと推定の糸, RE層追補）

> **状態: 実装済み（正本）**（2026-08-29 起票・同日実装。migration **076** —
> `atlas_edge_decisions`。実装記録は §11。Docker E2E は未実施）

**正本**: 本ドキュメント。
**親**: [分野マップの表示原則（討議記録）](../architecture/field_map_display_principles_2026-08-29.md)
— 原則①′「地形は人間・関係は離散の辺」の実装。
**関連**: [VA層](atlas_vector_anchoring_design.md)（VA1〜VA9 — アンカーベクトルの供給元。
本層は保存済みベクトルを**読むだけ**）/
[カテゴリギャップ候補](category_gap_candidates_design.md)（decide → preview → 教員 PUT →
mark-incorporated → freeze 刻印の弁 — 本層はその**辺版**）/
[知識ランドスケープ](knowledge_landscape_design.md)（配置共起の材料・数値非表示）。

---

## 1. 目的

骨格の辺（`SkeletonEdge`: adjacent / depends / related）は現在、教員が draft を
手書きする経路でしか増えない。一方 VA層のアンカーベクトルと配置データは
「この2概念は近い / 同じ論文群で共起する」という**関係の候補**を機械的に出せる。
本層はこれを2つの表示に接続する:

- **(a) 辺候補のレビューキュー**（教員）: 候補 → 教員確定（kind 選択）→
  既存の draft/freeze の弁を通って恒久配線になる。
- **(b) 推定の糸レイヤー**（学習者・教員）: 確定前の推定関係を点線 + 出所ラベル +
  骨格版明示のトグルレイヤーとして重ねる（既定オフ）。

## 2. 不変条項（RE1〜RE8）

- **RE1 主張は離散の辺のみ・地形不変**: 本層は node の位置・骨格の地形に一切
  触れない。表示に加わるのは名前付きの辺だけ。
- **RE2 出所必須**: 推定の辺（糸）は必ず点線 + 「AIによる推定（未確認）」系ラベル +
  骨格版を伴う。凍結された辺と視覚的に区別できない描画をしない。
- **RE3 恒久配線への経路は candidate → 教員確定 → 凍結のみ**: サーバ/AI が骨格
  draft を書く経路を作らない（AB4/KN-3 継承）。preview は patched_draft を返すだけで
  書かない。draft への反映は教員の既存 PUT（revision 楽観ロック）。
- **RE4 数値非表示**: cosine・共起件数を表示しない。近さは段階ラベル
  （`ANCHOR_NEARNESS_SCALE`）、共起の支持は論文タイトルの列挙（gap と同じ）。
- **RE5 情報を落とさない**: 判断は `atlas_edge_decisions` の status 遷移のみ
  （candidate / accepted / dismissed。restore あり・行削除 API なし・見送りは理由必須）。
- **RE6 候補は読み時導出・保存は判断のみ**: 候補スナップショットを持たない
  （gap 候補・PD5 と同じ）。導出は保存済みアンカーベクトル + 配置行の読みのみで、
  **embedding API を呼ばない**（学習者経路でも安全 = VA3/CR7 継承）。
- **RE7 ヘアボール防止**: 糸は concept–concept のみ・既存辺と同一 region 内ペアを
  除外・1ノードあたり上限2本・全体上限30本・閾値は最上位帯
  （`ANCHOR_NEARNESS_THRESHOLD_NEAR`）のみ。
- **RE8 教員の判断は学習者表示に反映される**: dismissed の辺候補は糸レイヤーから
  消える（見せない判断も判断）。凍結された辺は実線の骨格辺になり、糸からは
  自動的に消える（導出が既存辺を除外するため）。

## 3. DB（migration 076）

```sql
CREATE TABLE IF NOT EXISTS atlas_edge_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- edge|{domain_key}|{min(node_a,node_b)}|{max(node_a,node_b)}（無向・版非依存）
    edge_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'candidate'
        CONSTRAINT atlas_edge_decisions_status_check CHECK (status IN
            ('candidate', 'accepted', 'dismissed')),
    -- 採用時に教員が選ぶ辺種別（adjacent/depends/related。accepted のとき必須）
    edge_kind TEXT NOT NULL DEFAULT '',
    review_note TEXT NOT NULL DEFAULT '',
    -- 凍結で実際に反映された骨格の版（'' = 未反映。採用と反映の分離 — gap と同型）
    applied_version TEXT NOT NULL DEFAULT '',
    decided_by UUID REFERENCES users(id) ON DELETE SET NULL,
    decided_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_atlas_edge_decisions_status
    ON atlas_edge_decisions (status);
CREATE INDEX IF NOT EXISTS idx_atlas_edge_decisions_pending_apply
    ON atlas_edge_decisions (edge_key)
    WHERE status = 'accepted' AND applied_version = '';
```

gap decisions との差分: `merged` なし / `draft_node_id` なし（「draft に入ったか」は
draft の edges にペアが実在するかで判定 — 二重管理しない）/ `edge_kind` 追加。
シードなし・documents への FK なし（判断は分野の共同財）。

## 4. core（`backend/core/atlas_edges/`、FastAPI / LLM 非 import）

```
schema.py    → edge_key 正規化（build_edge_key / parse_edge_key — 無向: id を
               ソートして結合）/ ORIGIN_VECTOR="vector" / ORIGIN_CO_OCCURRENCE=
               "co_occurrence" / 定数（MIN_DOCUMENTS_FOR_EDGE=2・
               THREADS_MAX_PER_NODE=2・THREADS_MAX_TOTAL=30）
derive.py    → derive_edge_candidates(session, *, domain_key, skeleton, anchors)
               読み時導出。①vector: アンカーペアの cosine >= NEAR 閾値
               ②co_occurrence: 両ノードに live 配置（status NOT IN
               (superseded, rejected)）を持つ distinct document >= 2。
               除外: 既存骨格辺（無向一致）・同一 region 内ペア・region を含む
               ペア（v1 は concept–concept のみ）・骨格に無い node。
               DTO: {edge_key, from_id, from_label, to_id, to_label,
               origins: [...], nearness_label?, documents: [{document_id,
               title}], skeleton_version}
store.py     → decisions CRUD。**遷移は core/candidate_flow.py の CandidateFlow
               を使う（本番初適用）**: CandidateVocabulary(candidate, accepted,
               dismissed)・dismiss は理由必須・restore は dismissed→candidate。
               accept は edge_kind ∈ EDGE_KINDS を必須で保存。
               stamp_applied_versions(session, *, domain_key, frozen_version,
               frozen_edge_pairs) / list_pending_for_freeze(session, *,
               domain_key, draft_edge_pairs)（gap 同型・fail-open は route 側）
patching.py  → build_edge_patch(draft_skeleton, *, from_id, to_id, kind)。
               op=add / path="{prefix}/edges/-" / value={"from","to","kind"}
               （gap patching の _add_op と同形式）。draft に同一無向ペアが既に
               あれば EdgePatchError（validator に重複検査が無いため patching が
               防波堤）。endpoint が draft に無ければエラー
threads.py   → threads_for_domain(session, domain_key) → {"available", 
               "skeleton_version", "items": [{from, to, from_label, to_label,
               nearness_label}]}。vector 由来のみ（v1）・RE7 の上限・
               dismissed の edge_key を除外・(domain, version) キーの
               in-process キャッシュ（凍結版は不変なので TTL 不要。decisions の
               dismiss 反映はキャッシュ後段のフィルタで毎回）
```

candidate 生成に LLM ゼロ・embedding 呼び出しゼロ（保存済みベクトルの読みのみ）。
アンカー供給は `atlas_vectors.builder.anchors_with_labels(session, domain_key)`。

## 5. 管理 API（`routes/atlas_edges.py`、prefix="/cartridges"・main.py から
`/api/admin` で直接登録・全て `_require_teacher`・atlas_gaps と同型）

- `GET /{cid}/atlas/edge-candidates?include_dismissed=` →
  `{cartridge_id, candidates[], skeleton_version, draft_exists, draft_revision}`。
  各 candidate に `decision`（あれば）をマージ。
- `POST /{cid}/atlas/edge-candidates/decide`
  `{edge_key, action: accept|dismiss|restore, kind?: str, review_note?: str}`。
  accept は kind 必須（EDGE_KINDS 外は 422）・dismiss は理由必須（422）・
  domain 不一致 422・restore 対象なし 404。監査 `AUDIT_ENTITY_ATLAS_EDGE`。
- `POST /{cid}/atlas/edge-candidates/incorporate-preview` `{edge_key}` →
  `{patch, patched_draft, validation, revision, from_id, to_id, kind}`。
  読み取り専用（RE3）。decision が accepted でなければ 409・draft なし 409。
- `POST /{cid}/atlas/edge-candidates/mark-incorporated` `{edge_key}` —
  draft の edges に無向ペアが実在しなければ 409（教員 PUT の後に呼ぶ契約 —
  gap と同じ3段）。
- **freeze 統合**（`routes/atlas.py`）: gap と同列に ①pending ゲート
  （accepted かつ未反映かつ draft に無い辺があれば 409、`pending_edges` に
  「ラベルA — ラベルB」の列挙）②凍結トランザクション内で
  `stamp_applied_versions`（frozen の edges ペアで刻印）。どちらも fail-open の
  収集 + 数値なしの事実文。

## 6. 学習者向け: 推定の糸（threads）

- **配信**: `GET /api/atlas` レスポンスに optional top-level key `threads` を追加
  （route 層で fail-soft マージ。導出失敗・ベクトル不在・骨格なしはキー自体なし）。
  形は §4 threads.py の戻り値。**embedding API は呼ばない**（RE6）。
- **表示**: 新レイヤー `frontend/public/js/atlas-threads-layer.js`
  （landscape-layer.js と同じ3フック型: mountControls / onLevelRendered /
  onOverlayClosed。index.html に script 追加・atlas-overlay.js の3箇所に
  `if (window.AtlasThreadsLayer)` 呼び出し追加）。
  - トグル「推定の糸」**既定オフ**、`AtlasOverlay.data` の `threads` を読む
    （追加フェッチなし）。items ゼロ or キーなしならコントロールごと非表示。
  - **L2 のみ**描画: concept ノード間の**点線**（既存の実線骨格辺と明確に区別、
    stroke-dasharray）+ コントロール脇に事実文
    「AIによる推定（未確認）・骨格 版{v}」。ホバー等の追加 UI なし（v1）。
  - fail-closed: データなし・токен なしはチェックを戻す（landscape-layer 同型）。
- **学習者アンカー**: `atlas.relation-threads` を `core/help_kb/ui_anchors.py` +
  `docs/manual/student/02-student.md`（分野の地図の節配下に小節）+ 担体
  data-ui-anchor の3点セット。

## 7. 管理 UI

`atlas-reports-section` の**第3グループ**「関係（辺）の候補」（buildGapsGroup と
同じ後付けパターン・admin.html 非変更）。カード:
`{from_label} — {to_label}`、出所チップ（「プロトタイプ近傍（{nearness_label}）」/
「共起: タイトル列挙」）、kind 選択（隣接/依存/関連 — 正本は
`label_vocab.EDGE_KIND_LABELS`）+ [採用] / [見送り（理由必須）] / 見送り済み
フィルタ + [下書きへ反映]（preview → 事実文 confirm → 既存 PUT → mark-incorporated —
gap の gapApplyIncorporation と同じ流れ）。アンカーは
`atlas.edge-candidates` / `atlas.edge-dismissed-filter` / `atlas.edge-incorporate`
の3件 + 教員マニュアル節（17-admin-atlas.md）。

## 8. 語彙・監査

- `label_vocab.EDGE_KIND_LABELS = {"adjacent": "隣接", "depends": "依存",
  "related": "関連"}`（enum→日本語の正本。フロントはミラー規律）。
- 監査 `AUDIT_ENTITY_ATLAS_EDGE = "atlas_edge"`（カタログ40語彙目）。action は
  decide 系（accept / dismiss / restore）+ mark_incorporated。読み時導出のため
  detect 記帳はなし。

## 9. ガードレール

`test_atlas_edges_{core,api,guardrails,ui_static}.py`:
core 非 FastAPI・骨格書込ゼロ（RE3）・embedding/LLM 非接触（RE6）・DELETE 不在
（RE5）・数値非漏洩（RE4）・糸の上限と除外規則（RE7）・dismissed 除外（RE8）・
点線と出所ラベルの担体（RE2）・migration⇄schema 語彙一致・CandidateFlow 経由の
遷移（直書き遷移の禁止）・freeze ゲートと刻印の静的検査・学習者トグル既定オフ。

## 10. 非スコープ（v1）

- co_occurrence 由来の糸（学習者表示は vector のみ。教員候補には両方出す）
- region を含む辺候補・辺の削除/改名候補（additive-only 継承）
- 糸のホバー詳細・クリック遷移 / L1・L3 への描画
- 深さ（係留プロファイル）との連動 / node2vec 等のグラフ埋め込み由来の候補
- 辺候補の G層 To-Do ルール

## 11. 実装記録（2026-08-29、v1 全 Phase 同日実装）

Fable 5 指揮 + Opus 5 サブエージェント4体（core / 管理UI / 学習者糸レイヤー / API）。
設計からの逸脱・確定事項:

- **candidate_flow の本番初適用**: `atlas_edges/store.py` の遷移は
  `core/candidate_flow.py`（`CandidateVocabulary` + `CandidateFlow`）経由。
  `record_audit` は注入（core は api を import しない）。route 側は collector 方式 —
  flow が commit 前に呼ぶ audit ペイロードを溜め、**commit 成功後にのみ**
  `theory_review_events` へ記帳する。decide の返す action / 監査 action は API 語彙
  （accept / dismiss / restore）。行は初回 decide 時に candidate として遅延 INSERT
  （ON CONFLICT DO NOTHING）・UPDATE は `status = :old_status` ガード付き
  （lost update は事実文 ValueError → 422）。
- **patch 適用**: `atlas.skeleton_to_dict` は常に `edges` キーを出すため、既存
  `core.atlas_generator.apply_json_patch` の add 分岐（末尾トークン `-`）が
  `/atlas_skeleton/edges/-` にそのまま効く — 専用アプライヤは追加していない
  （テストで固定）。patching 側のガード: kind 語彙・端点実在・無向重複・自己ループ
  （骨格 validator に重複/自己ループ検査が無いため patching が防波堤）。
- **mark-incorporated は検証 + 監査のみ**: draft の edges に無向ペアが実在するかが
  「反映済み」の唯一のマーカー（専用列なし・二重管理しない）。status は accepted の
  まま。凍結時に `stamp_applied_versions`（凍結版の edges ペア照合）が
  `applied_version` を刻印し、freeze 監査 metadata に `relation_edges_applied` として
  同乗（gap の `category_gaps_applied` と同型）。
- **freeze ゲート**: gap ゲートの直後に辺版 409（`pending_edges` はラベル
  「A — B」列挙・数値なし）。収集は fail-open（例外 → []・freeze を止めない）。
- **threads**: `GET /api/atlas` の route 層で fail-soft 合流（session None ガード +
  lazy import + except → キーなし）。導出は `atlas_vectors.builder` を
  `threads._candidate_pairs` 内で lazy import（import 時に core.llm を引かない）。
  ペア計算は (domain, version) キーの in-process キャッシュ・dismissed 除外は
  キャッシュ後段で毎回。描画は `atlas-threads-layer.js` — レイヤー3フック型・
  L2 のみ・座標は renderL2 が出力した実 DOM（`.atlas-node[data-node]` の circle）を
  参照（レイアウト計算の二重実装をしない）・専用 `<g class="threads-layer">` で
  非破壊・`stroke-dasharray "2 5"`・既定オフ・localStorage 不使用。
- **管理UI**: gap 群の完全ミラー（後付けグループ・prompt() による理由必須・
  preview → confirm → `applyAssistProposal(patched_draft)` → mark-incorporated）。
  kind 選択は `edgeKindSelections` で再描画をまたいで保持。
- **アンカー**: 管理 313→**316**（graph-review WIP が先に 313 まで使用済みだった）+
  学習者 25→**26**（`atlas.relation-threads`）。
- **テスト**: `test_atlas_edges_{core(75),guardrails(35),api(42),admin_ui_static(46)}.py` +
  `test_atlas_threads_ui_static.py`(26) + atlas_view 挙動3件。
- **既知の限界**: ①threads キャッシュはプロセスローカル（dismiss の反映は即時だが
  ペア集合は再起動まで版キーで保持 — 凍結版不変なので問題にならない）②co_occurrence
  は配置の質（inferred 込み）に依存 — 教員候補のみで学習者には出さない（RE8/§10）
  ③Docker E2E（migration 076 実適用・freeze 実走）未実施。
