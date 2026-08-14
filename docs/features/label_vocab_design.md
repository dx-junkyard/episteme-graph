# 段階ラベル辞書の正本（`core/label_vocab.py`）

**状態:** 実装済み（2026-08-14）— 正本・凍結。本書が `backend/core/label_vocab.py` の正本。
以後の変更は §8 実装記録への追記で行う。

**位置づけ:** [機能整備提案 2026-08-13](../architecture/feature_consolidation_proposals_2026-08-13.md) §2-2
の実装。`core/privacy.py`（k-匿名の閾値正本）・`core/element_vocab.py`（統制語彙の訳語正本）・
`core/candidate_flow.py`（candidate→confirm の制御フロー）と並ぶ「横断基盤」の1本。

---

## §1 目的

「生値（confidence / weight / 件数）を画面に出さず段階ラベルにする」は W8 / LS5 / FG8 / P7 /
SL4 と各層の不変条項に書かれた**共通の掟**だが、その実装は層ごとに独立していた。

実測（2026-08-13 の偵察）で分かった実態は2つ:

1. **変換規則の重複** — 同じ 0.75 / 0.5 の境界と同じ try/except を4箇所が別々に持ち、
   同一の日本語表（`_SUPPORT_SECTION_LABELS`）が3箇所でバイト一致していた。
2. **重複はフロントとの二重管理ではない** — 生値→ラベルの変換はサーバに100%あり、
   フロントには1件も無かった。フロント側にあるのは「サーバの語彙 enum を日本語にする表」で、
   これは**ミラー**（正本はサーバ）として固定するのが正しい。

本モジュールは (1) の変換規則と共有表を引き受け、(2) はミラーテストで固定する。

**共有するもの**:

- 段階の境界値（しきい値の並び）と、そこから段階ラベルを引く1つの関数
- 「未測定（`None`）・数値化できない値は**最も慎重な段階**へ倒す」不変条項
- 複数レイヤーでバイト一致していた日本語表（`SUPPORT_SECTION_LABELS` ほか）
- 状態投影語彙（`core/status/schema.py`）の訳語 — routes 層にしか無かったものを core へ

**共有しないもの**（意図的に各層へ残す）:

- 生値の**正規化**（範囲外を破棄するか `[0,1]` へクランプするか。層ごとに違う判断）
- パーセンタイル型の段階化（`core/doubt/schema.py::load_level_for_score`）
- k-匿名と複合した段階化（`core/reconstruction/health.py::rate_level`。境界の向きも逆）
- 閉世界語彙の事実文（`core/doubt/support_paths.py` の `FACT_LINE_*`。SL1 で原文固定）
- 同名別ロジックの関数（`_endorsement_label` ×2）
- k-匿名の閾値・件数レンジ（`core/privacy.py` が正本）

## §2 不変条項

| ID | 内容 |
|---|---|
| LV1 | **情報が無いことを高確度に見せない**。`None` / 非数値 / 変換不能は必ず末尾（最も慎重な）ラベル。全スケール共通でテストが固定する。 |
| LV2 | **出力文字列を変えない**。本モジュールの新設は委譲の統合であり、既存の API 応答・UI 文言は1文字も変えていない。 |
| LV3 | **宛先ごとの文言差は統合しない**。同じキーで文言が違う表は「別名2表として並べて可視化」する（無言の1本化は出力を変える）。 |
| LV4 | **純粋**。FastAPI / sqlalchemy / core.postgres / openai / A層 agents を import しない。 |
| LV5 | **正規化を持ち込まない**。段階ラベル化と正規化は別の判断（§1）。 |
| LV6 | **フロントの語彙表は正本ではない**。サーバ側に正本を置き、ミラーテストで逐語一致を固定する。片側だけの変更はテストで落ちる。 |

## §3 API

```python
@dataclass(frozen=True)
class GradedScale:
    thresholds: tuple[float, ...]   # 降順
    labels: tuple[str, ...]         # 上位から。len == len(thresholds) + 1

    @property
    def cautious_label(self) -> str  # 末尾ラベル（未測定の行き先）
    def label_for(self, value: object) -> str
```

宣言済みスケール:

| 名前 | 境界 | ラベル | 利用 |
|---|---|---|---|
| `CONFIDENCE_LOW_MED_HIGH` | 0.75 / 0.5 | 高 / 中 / 低 | `core/teaching_figures/schema.py`・`core/atlas_gaps/schema.py` |
| `CONFIDENCE_TENTATIVE_REFERENCE_HIGH` | 0.75 / 0.5 | 確度高 / 参考 / 暫定 | `core/deliberation/identity_links.py` |
| `WEIGHT_LEVEL_SCALE` | 0.7 / 0.4 | strong / medium / weak | `core/landscape/schema.py::weight_level` |
| `WEIGHT_RELATION` | 0.7 / 0.4 | 強い関連 / 関連 / 弱い関連 | `core/landscape/schema.py::weight_label` |

共有表: `SUPPORT_SECTION_LABELS` / `VERIFICATION_STATUS_LABELS_LEDGER` /
`VERIFICATION_STATUS_LABELS_LENS` / `MATERIAL_STATE_LABELS` / `SCRIPT_STATUS_LABELS` /
`AUDIO_STATUS_LABELS` / `WEIGHT_LABELS`。

## §4 委譲した箇所（W1）

| 移行元 | 公開名の扱い |
|---|---|
| `core/teaching_figures/schema.py` | `confidence_label` / `CONFIDENCE_LABELS` / `CONFIDENCE_LABEL_*` を再エクスポートで維持 |
| `core/atlas_gaps/schema.py` | 同上 + `CONFIDENCE_THRESHOLD_HIGH` / `_MEDIUM` |
| `core/deliberation/identity_links.py` | `confidence_label` / `CONFIDENCE_LABEL_TENTATIVE` / `_REFERENCE` / `_HIGH` |
| `core/landscape/schema.py` | `weight_level` / `weight_label` / `WEIGHT_LABELS` / `WEIGHT_THRESHOLD_*` / `WEIGHT_LEVEL_*` |
| `core/deliberation/positioning.py` | `_SUPPORT_SECTION_LABELS` / `_VERIFICATION_STATUS_LABELS`（LENS 側） |
| `core/deliberation/context_lens.py` | `_SUPPORT_SECTION_LABELS` |
| `core/discuss/opening.py` | `_SUPPORT_SECTION_LABELS` |
| `api/routes/doubt.py` | `_VERIFICATION_STATUS_LABELS`（LEDGER 側） |
| `api/routes/admin_assistant.py` | `_MATERIAL_STATE_LABELS` / `_SCRIPT_STATUS_LABELS` / `_AUDIO_STATUS_LABELS` |

いずれも**モジュール内の名前を保ったまま**正本を指すだけの変更で、import 面・出力文字列は不変。

## §5 フロントの語彙ミラー（W2 / W3）

- `core/doubt/schema.py` に、`doubt-atlas.js` が自前で持っていた表のサーバ正本を新設した
  （`COVERAGE_LABELS` / `CHALLENGE_STATUS_LABELS` / `PROPOSAL_STATUS_LABELS` /
  `SUPPORT_LEVEL_BADGE_LABELS` / `FALSIFICATION_ASPECT_LABELS` / `COVERAGE_LEVELS`）。
  文言は**現行フロントの逐語**で、サーバの挙動は変えていない（canon の宣言のみ）。
- `doubt-atlas.js` の `DOUBT_TYPE_LABELS` は**参照ゼロの死表**だったので削除した。学習者画面は
  API の `doubt_type_label` をそのまま描き、正本は `core/structure_anchor/schema.py`。
  「表が無いこと」を負のアサーションで固定している。
- `admin-lecture-studio.js` に**リテラル NUL バイト**が1つ埋まっていた
  （`var sig = sTitle + "\x00" + sSummary;` の区切り文字が実バイト）。grep がこのファイル全体を
  バイナリ扱いし `--include=*.js` の一斉調査から**無言で**外れていた。JS エスケープ
  `"\x00"`（挙動同一）へ置換し、`*.js` に NUL が無いことをガードレールで固定した。

### 意図して委譲しなかった2件

- **`admin.js::LANDSCAPE_STATUS_FALLBACK_LABELS` ≡ `admin-release-review.js::STATUS_FALLBACK_LABELS`**:
  サーバの `status_label` が取れないときの fail-soft フォールバック。共有参照にすると
  「相手ファイルが未読み込みでも成立する」という目的自体が失われるため2表のまま残し、
  **バイト一致をミラーテストで固定**した。加えて `confirmed` / `inferred` / `review_required`
  の3値は `core/landscape/schema.py::PROVENANCE_LABELS` と逐語一致することを固定する
  （AC-005: AI 推定を教員確認済みに見せない一線はフォールバック経路でも崩さない）。
- **`admin-lecture-studio.js::genericTitles`**: `ElementVocab.kindLabel` への委譲は
  `test_learning_material_embed_resolution.py` の Node harness が `lsTopicEvidenceItems` を
  単体抽出して評価する構造（`window` を持たない）と両立しない。表は残し、
  `element-vocab.js::KIND_LABELS` との一致をミラーテストで固定した。

## §6 ガードレール

`backend/tests/test_label_vocab_guardrails.py`（7項）:

1. 非依存（FastAPI / sqlalchemy / core.postgres / openai / episteme_graph 非 import・`get_session` 不在）
2. 境界値（0.75 / 0.74 / 0.5 / 0.49、0.7 / 0.69 / 0.4 / 0.39）と LV1（`None` / `"abc"` / `-1` /
   `-inf` / 任意オブジェクト → 末尾ラベル）、スケール形状の検証
3. 委譲先9モジュールが**同一文字列**を返し続けること（LV2）
4. 移行済みモジュールに段階ラベルのリテラル再定義が復活していないこと
5. **重複表検出**: `backend/core` + `backend/api` の module-level を ast で走査し、値が全て
   日本語の表がバイト一致で2箇所に存在したら fail（許容リストは**空**）
6. **黙った分裂の検出**: 同じキー集合で値が違う表は理由コメント付き allowlist に登録が必要。
   現在6グループ（`atlas_path` の状態ラベル×台帳注記 / discuss 開幕の stage 表示名×統制語彙訳 /
   本モジュールの verification status 2表 / `personas` の2プロンプト / `reconstruction/diff` の
   3テンプレート / `teaching_figures` の図タイプ名×ギャップ説明）。allowlist は「存在しない表が
   残っていないか」も検査して墓場化を防ぐ
7. `frontend/public/js/*.js` にリテラル NUL バイトが無いこと

`backend/tests/test_doubt_vocab_mirror.py`（11項）: `doubt-atlas.js` の9表 ⇄ Python 正本の逐語
一致、キー集合とサーバ語彙（enum / `*_LEVELS`）の一致、`CHALLENGE_TYPE_LABELS` はフロントが
「…という疑義」を付す関係の固定、`DOUBT_TYPE_LABELS` 不在の負のアサーション、
フォールバック2表と `genericTitles` のミラー。

## §7 非スコープ

- **訳語の統一そのもの**（例: theory stage の「方程式系」/「式の体系」、
  「AI推定（未確認）」/「AIによる推定（未確認）」）— 出力文字列を変える判断であり、既存の静的
  テストが原文を固定しているためオーナー判断事項として繰り延べ。ガードレール6が
  allowlist として**分裂を可視化**するところまでを本作業の範囲とする。
- **語彙エンドポイント**（select / SVG 用の語彙をサーバから配信する）— 同上。
- フロント側の表の全廃（API が段階ラベルを返す形への移行）。
- `core/privacy.py`（k-匿名）・`core/element_vocab.py`（統制語彙訳）の統合。役割が違う別正本。

## §8 実装記録（2026-08-14）

- `backend/core/label_vocab.py` 新設（`GradedScale` + 4スケール + 7表）
- 委譲差し替え9ファイル（§4）。公開名は再エクスポートで維持し、既存テストは無修正で green
- `backend/core/doubt/schema.py` にフロントミラーの正本5表 + `COVERAGE_LEVELS` を追加
- `frontend/public/js/doubt-atlas.js`: 死表 `DOUBT_TYPE_LABELS` 削除 + 正本注記
- `frontend/public/js/admin-lecture-studio.js`: リテラル NUL → JS エスケープ（挙動同一）
- `backend/tests/test_label_vocab_guardrails.py`（35 tests）/
  `backend/tests/test_doubt_vocab_mirror.py`（11 tests）新設
- 発見事項として記録: 重複表（バイト一致）は本作業後 **0 件**。同キー・異値の分裂は 6 グループで、
  うち `core/discuss/opening.py::_STAGE_LABELS` ⇄ `core/element_vocab.py::THEORY_STAGE_LABELS`
  は**訳語の実質的な分裂**（`equation_system`: 方程式系 / 式の体系）。§7 のとおり統一は繰り延べ。
