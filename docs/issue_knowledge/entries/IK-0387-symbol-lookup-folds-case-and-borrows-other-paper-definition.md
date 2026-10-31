---
id: IK-0387
title: "記号の「直前の定義」が λ と Λ を同じ記号として照合し、別の論文（連星ブラックホール）の Λ の文を Cep B 論文の λ の定義として返したうえ、タップ位置が無いのに「この節の中」と表示する"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が式の記号をタップして定義を見る（概念レジストリ P3-5）
  layers: [concept_registry, frontend_learning_ui]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [meaning, target]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: 記号の照合に概念名の正規化 concept_normalizer.normalize_key を流用しており、これは大文字小文字を畳み
    Λ / λ / \Lambda / \lambda をすべて "lambda" にする（input_handling）。接続軸: ①「lambda」という同じキーが
    質量対磁束比 λ と選択関数の引数 Λ という別の量を指す（meaning）②タップ位置の論文はタップした式 ID からしか
    求めておらず、チャンク ID からは求めていなかったため、同じコースの別論文の記号行へ無言で倒れた。有効範囲
    「この節の中」は記号行の属性で、タップ位置が無い・別論文の記号では「どの節か」を指せない（target。medium:
    論文を引く経路は2つの原因の合成）。確認: 第 8 周で学生が \lambda を照会し、source=2606.02318v1・
    definition="…ξ(Λ) is the selection function…"・scope_label="この節の中" を受け取った。
generalization:
  level: repo_pattern
  general_form: 文脈の違う正規化（概念名向け）を別の対象（記号）に流用し、区別すべき名前を同じキーに畳む
pattern: same-name-different-referents
discovery:
  perspective: [reproduction]
  note: 物理学出身の学生が「大文字の Λ の話に見える」「どの節か分からない」と具体的に指摘した。
resolution:
  perspective: [representation_change, carry_through]
  note: >-
    記号専用の照合キー symbol_key を symbol_lookup.py に置いた（大文字小文字を保つ。TeX ⇄ Unicode のギリシャ文字・
    波括弧・書体指定 \rm / \mathrm 等・空白・$ だけを畳む。完全一致のまま）。タップ位置の論文は式に加えてチャンクからも
    求め、同じ論文の記号行を先に当てる。別の論文の定義へ倒すときは論文をまたいで前後を比べず、
    FACT_DEFINITION_FROM_OTHER_DOCUMENT（論文タイトル入り）を1文だけ添える。タップ位置が無いときは出所の論文タイトルを
    FACT_DEFINITION_FROM で添え、「この節の中」「この式の中だけ」は出さない（「論文全体」は出す）。
    concept_normalizer.normalize_key 自体は概念名の用途で他が使っているので変えていない。
  landed_in:
    - backend/core/symbol_lookup.py
    - backend/tests/test_symbol_lookup_core.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での \lambda / λ の再照会（Cep B 論文の登録簿に λ の行があるか）
      - 記号登録簿の definition_evidence_texts に別の記号の定義文（ξ の定義）が入っていた件の原因（A層 symbol_registry 側。追っていない）
      - 第 8 周の \lambda 応答は facts が空だった（ホストのコードでは位置不明の事実文が付く）。砂場の API イメージとホストのコードの差は確かめていない
related: [IK-0380, IK-0386, IK-0377]
view_of: []
history: []
---

## 課題

λ と Λ が同じ記号として照合され、別論文の文が定義として出る。有効範囲の表示が画面の文脈と合わない。

## 発見の観点

実ペルソナの具体的な指摘（第 8 周）。

## 解決の観点

記号専用の大文字小文字を保つ照合キーに置き換え、タップ位置の論文を運び、倒したときは出所を言う。

## 一般化

正規化は対象ごとに意味が違う（概念名は大文字小文字を畳んでよいが記号は畳めない）。
