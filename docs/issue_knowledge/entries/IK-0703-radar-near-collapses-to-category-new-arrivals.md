---
id: IK-0703
title: "論文レーダーの距離「近い」が、承認済み部品ゼロの起点教材ではキーフレーズ無しのカテゴリ新着に黙って縮退し、「近い論文」として並ぶ"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md §17"
  - "docs/features/paper_radar_design.md §17.1"
  - "docs/features/paper_radar_design.md §21"
feature_context:
  realizing: "教員が自分の教材を起点に、主題の近い arXiv 論文を探す"
  layers: [paper_radar, paper_discovery]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [contract]
    governance: [completion]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（自律周回の検索記録 2 起点と radar の供給コード）: キーフレーズの供給元が承認済み理論部品だけで、承認は教員の後工程の
    ため解析直後の教材では必ず空になり、検索はカテゴリの OR だけで走った。統制軸 completion = 「承認済み部品がある」ことを
    「検索条件が組めた」の代理にし、供給が空でも near として完了扱いにしていた。接続軸 contract = 最初の是正で足した主張の概念は、
    検索語としての契約（英字・語数・記号なし）を供給側が確かめておらず、文や数式片が arXiv クエリに入って該当ゼロになった
    （前段の出力と後段の入力の片方だけを変えた）。処理軸 none = 帯分け・クエリ組み立ての各処理は入力に対して正しく動いていた。
    構造軸 none = 供給の段を足すだけで表現の変更は要らなかった（語彙表への追加は既存の受け皿）。
generalization:
  level: general
  general_form: "条件を組む材料が空のとき、絞り込み無しの広い既定で処理を完了させ、その結果を絞り込んだ結果と同じ名前で示す"
pattern: completion-defined-by-proxy
discovery:
  perspective: [data_inspection, reproduction]
  note: "自律周回の検索記録で、2 つの起点とも near のクエリがカテゴリの OR だけで、主題を扱う候補がほぼ無いことを読み、教員がキーフレーズを手で足した 1 回と比べて縮退を確かめた（出所: uxsim/runs/radar_loop/judge-scan-01.md・judge-scan-02.md・scan-01.json・scan-02.json・scan-03.json・scan-05.json）。"
resolution:
  perspective: [carry_through, explicit_contract]
  note: "供給の段を承認済み部品 → 確認済み配置 → 主張の概念 → 題名と中心命題の名詞句に広げ、題名・中心命題の段は前段の有無に関わらず常に走らせる。主張の概念は検索語の契約を供給側で検査する。供給の出所と縮退は事実文で示す。"
  landed_in:
    - backend/core/paper_discovery/radar.py
    - backend/core/paper_discovery/schema.py
    - backend/core/label_vocab.py
    - "docs/features/paper_radar_design.md §17"
    - backend/core/paper_discovery/search.py
    - "docs/features/paper_radar_design.md §21"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "ブラウザでのキーフレーズ区画と事実文の表示"
      - "日本語の中心命題しか持たない教材での供給"
related: [IK-0704, IK-0706]
view_of: []
history: []
---

## 課題

症状: 論文レーダーで距離「近い」を選んでも、キーフレーズ候補が空で arXiv クエリはカテゴリの OR だけになり、カテゴリの新着が
「近い論文」として並ぶ。2 つの起点（宇宙論の理論・素粒子現象論）で再現し、教員が手で 4 語を足すと主題の候補がほとんどを占めた。
原因: キーフレーズの供給が承認済み理論部品だけで、承認前の教材（レーダーを使う時点では普通）では空になる。最初の是正で主張の
概念を供給に足したところ、記号を除いた残りが文や数式片で、それが検索語に入って該当ゼロになった（`scan-03.json`）。実データで
確かめて検索語の契約を供給側で検査し、題名・中心命題を常に走る段にして再是正した（`scan-05.json` で主題の候補が揃った）。

出所: uxsim/runs/radar_loop/judge-scan-01.md・judge-scan-02.md、検索記録 scan-01.json / scan-02.json / scan-03.json / scan-05.json。

## 発見の観点

実データの検索記録を読み（data_inspection）、手でキーフレーズを足した検索と並べて縮退を再現した（reproduction）。

## 解決の観点

供給の段を足して条件の材料を後段まで運び（carry_through）、検索語としての契約を供給側で明示的に検査した（explicit_contract）。
承認前の部品（candidate）は使わない（確定は人）。主張の概念は出所付きのチップにし、教員が外せる。

## 一般化

「前段が何か返した」「承認済みのものがある」を「条件が組めた」の代理にすると、材料が空・壊れたときに広い既定で完了したように
見える。確かめ方は、承認ゼロ・記号だらけの主張という不利な実データの形を写したテストを置き、実機で検索語とクエリを読むこと。

## 再々是正（2026-10-16・第 4 波 §21 A）

連鎖した起点（2610.08908・`uxsim/runs/radar_loop/scan-06.json`）で、題名・中心命題・要旨の段から `P` / `B` / `Gpc` /
`averaging` と 5 語の概念「Fisher forecast of covariance-modeling impact」が供給され、網が大きく広がった。中心命題の数式区切りが
崩れていて式の中の 1 字が「丸ごと 1 語の句」として採られ、単位が文中の大文字語として固有名に数えられていた。供給側の契約を
`radar.is_quality_keyphrase` に足した（1 語は 3 字以上・中心命題と要旨から取った 1 語は大文字を含む・単位と「数字 + 単位」と
一般動詞の動名詞を除く・概念名は 4 語 40 字まで）。併せて、一致チップと `keyphrase_hits` の照合（`search.matched_keyphrases`）が
大小無視の部分一致で `P` / `B` が「全候補に当たった」ように見えていた（照合の人工物・`rule-enforced-per-surface`）ので、語境界付きの
正本 `episteme_graph.agents.alias_matching.text_mentions_alias` に寄せた（3 字以下は大小区別の単語完全一致。分野購読の一致チップにも効く）。

