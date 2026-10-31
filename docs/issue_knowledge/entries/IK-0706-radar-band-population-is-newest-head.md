---
id: IK-0706
title: "論文レーダーの帯分けの母集団が「網の中で取得した新着の先頭」だけで、帯ラベル「近い」がその中での相対的な近さしか意味しない"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-22
sources:
  - "docs/features/paper_radar_design.md §24"
  - "docs/features/paper_radar_design.md §19.2"
feature_context:
  realizing: "近い論文の候補を近さの段階（近い・中間・遠い）に分けて示す"
  layers: [paper_radar, paper_discovery]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [meaning]
    governance: [budget]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（検索記録と帯分けの手順）: 帯分けは取得後の候補にだけ働き、取得は網の中の新着の先頭に限られる。このため「近い」は
    主題との近さではなく取得した候補の中での相対的な位置を指す。接続軸 meaning = 帯ラベルの意味が、ラベルを読む段（主題との近さ）
    と算出する段（取得範囲内の相対比較）でずれる。統制軸 budget = 取得件数は外部 API の呼び出し予算で抑えられており、件数を
    増やして母集団を広げる解は予算に触れる。処理軸 none = cosine と帯の閾値の計算は正しい。構造軸 none = 表現の変更ではなく
    母集団の性質の問題。
generalization:
  level: general
  general_form: "段階ラベルを付ける母集団を近くの手がかり（新しさの順の先頭）で決め、ラベルが全体に対する位置のように読まれる"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection]
  note: "手でキーフレーズを足して網を狭めた検索でも、起点の核心に当たる論文を拾う保証が無く、ある起点ではむしろ「中間」の帯の方が近かったことを検索記録で読んだ（出所: uxsim/runs/radar_loop/judge-scan-01.md §4 仮説 2・judge-scan-02.md §4 の 4・scan-01b-manual-keyphrases.json）。"
resolution:
  perspective: [explicit_contract]
  note: "母集団の取り方（新着の先頭・取得件数）は予算に触れるので変えず、帯ラベルが出るときは関係ラベルの有無に関わらず常に母集団の事実文（radar.NOTE_BAND_POPULATION「帯は arXiv から取得した新着の候補の中での比較です。取得していない論文はこの帯に入りません。」）を band_population_note として返し、凡例の直後に 1 回だけ描く。帯分けできなかったときは出さない。数値は出さない（PR2 / PD4）。対照検索（contrast-2026-10-22-kurtosis.json）で帯付き候補とともに返ることを確かめた。§20 D の reading_note は関係ラベルと同時に出るときの読み方として残す。"
  landed_in:
    - "backend/core/paper_discovery/radar.py"
    - "frontend/public/js/admin-paper-radar.js"
    - "backend/tests/test_paper_radar_core.py"
    - "backend/tests/test_paper_radar_ui_static.py"
    - "docs/features/paper_radar_design.md §24"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "再演は 2026-10-22 のオーケストレータの対照検索（uxsim/runs/radar_loop/contrast-2026-10-22-kurtosis.json・尖度の起点・作業ツリーを反映）で band_population_note が返ることを見たもの。画面で帯と凡例の近くに 1 行で読めること（ブラウザは開いていない）"
      - "母集団そのもの（新着の先頭に限られること）は変えていない。ラベルを読む人がこの文で相対比較と読むかは未確認"
related: [IK-0703]
view_of: []
history: []
---

## 課題

症状: 帯ラベル「近い」が主題の近さではなく「取得した候補の中で相対的に近い」を指し、ある起点では「中間」の帯の方が主題に近かった。
原因: 取得は網の中の新着の先頭に限られ、帯分けは取得後にしか働かない。母集団の性質が画面に書かれていない。

出所: uxsim/runs/radar_loop/judge-scan-01.md §4 仮説 2・judge-scan-02.md §4 の 4、検索記録 scan-01b-manual-keyphrases.json。

## 発見の観点

実データの検索記録で帯と主題の対応を読んだ（data_inspection）。

## 解決の観点

未解決。①網をキーフレーズで狭める（IK-0703 で供給を是正済み）②帯の母集団の性質を事実文で書く、の順で考える。取得件数を
増やす案は外部 API の予算に触れるので採らない。

2026-10-16 追記（第 3 波・`docs/features/paper_radar_design.md` §20 D）: 帯の読み方の事実文で**部分対応**した。検索結果に
`reading_note`（`radar.READING_NOTE_BANDS_AND_RELATIONS` + 並び順の文）を足し、帯と関係の種類（AI 比較）が同時に画面に出るときだけ
「距離帯は取得した候補の中での要旨どうしの近さ、関係の種類は AI が要旨を読んだ結果」と並べ順の事実を 1 行で言う。
**母集団の問題は残る**（取得が網の中の新着の先頭に限られること自体は変えていない — 取得件数は §14 の予算に触れる）。辞書に「段階ラベルの母集団の取り方」を表す専用の型が無いため、範囲を近くの手がかりで
決める型 extent-decided-by-proxy-signal に当てた（判定の代理という面では completion-defined-by-proxy にも近い）。

## 一般化

相対的な段階ラベルは、母集団の決め方が見えないと絶対的な位置として読まれる。検索・推薦・ランキングの段階表示で再発しうる。
