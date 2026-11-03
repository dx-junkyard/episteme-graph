---
id: IK-0802
title: "論文レーダーのキーフレーズ供給が多義の短い略語（EFT）を単独で供給し、未取り込み候補がすべてその語だけの一致で主題外へ広がったのに、網を広げる語の判定（すべての候補に当たった）に掛からず印も事実文も出なかった"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md §23.5"
feature_context:
  realizing: "起点の論文の主題を表す語で arXiv を検索し、近い論文の網を取り、網を広げている語を教員に示す"
  layers: [paper_radar, paper_discovery]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（実 DB の起点 3 本で供給関数を呼び、scan-09 の候補に当たり判定を掛け直した）: EFT は中心命題の日本語の定義
    「有効場理論（EFT）」から反復語・固有名として供給され、英語のフル表記は本文（chunks）にしか無かった。網を広げる語の判定は
    「すべての候補に当たった」だけで、単独一致が過半という形を拾わなかった。処理軸 input_handling = 起点の材料から検索語を
    切り出す段で、多義の略語をフル表記に置き換える材料（本文）を読んでいなかった。接続・構造・統制は none。
generalization:
  level: general
  general_form: "テキストから検索語を決定論で切り出すとき、短い略語を単独で供給すると分野をまたぐ同綴りの略語に一致して OR 検索の網が広がり、網の広がりの判定が代理条件（すべてに当たった）だけだとその広がりを言えない"
pattern: upstream-loss-masked-downstream
discovery:
  perspective: [data_inspection]
  note: "自律周回の記録 scan-09.json と審判 judge-scan-09.md で、未取り込み候補 7 件がすべて EFT 単独一致・うち 5 件が主題外なのに keyphrase_hits の widens が全語 false であることを読んだ（出所: uxsim/runs/radar_loop/scan-09.json）。"
resolution:
  perspective: [representation_change, guardrail_fix]
  note: "大文字だけの 2〜3 字の略語は、採る位置でフル表記の句（材料の「フル表記 (略語)」→ 起点の chunks の「フル表記 (略語)」）を先に試し、フル表記かそれを含む句が供給されたら略語を落とす。見つからなければ略語を残す。一般語の表は作らない。網を広げる語の判定に「この語だけに一致した候補が過半」を足し、その語に widens_note の事実文を付け、画面のチップに印を付けた。"
  landed_in:
    - backend/core/paper_discovery/radar.py
    - frontend/public/js/admin-paper-radar.js
    - backend/tests/test_paper_radar_core.py
    - backend/tests/test_paper_radar_ui_static.py
    - "docs/features/paper_radar_design.md §23.5"
  verification:
    methods: [guardrail, scratch_db]
    unverified:
      - "是正後の供給語（effective field theory）で arXiv を検索したときの網（arXiv は呼んでいない）"
      - "ブラウザでのチップの印の表示"
related: [IK-0800]
view_of: []
history: []
---

## 課題

症状: 起点 2609.35757「Can Fingers of God be Resummed?」の near で、§23 の是正後も供給語に EFT が残り、未取り込み候補 7 件が
すべて EFT だけで一致し、うち 5 件がアクシオン・非弾性暗黒物質・高階微分重力・インフレーションの主題外だった。網を広げる語の
印（widens）は全語 false で、画面はこの広がりを名指ししなかった。
原因: EFT は中心命題・目的の日本語の定義「有効場理論（EFT）」から来ており、英語の「フル表記 (略語)」は材料（題名・中心命題・
目的・要旨）に無く、本文にしか無かった。網を広げる語の判定は「取得した候補のすべてに当たった」だけで、取り込み済みの 1 件が
EFT を含まないと掛からなかった。

出所: uxsim/runs/radar_loop/scan-09.json / judge-scan-09.md。

## 発見の観点

自律周回の記録の供給語・候補ごとの一致語・keyphrase_hits を読んだ（data_inspection）。

## 解決の観点

短い略語をフル表記の句に置き換える表し方に変えた（representation_change）。scan-09 の形を写したテストで固定した（guardrail_fix）。
実 DB の起点 3 本（c795b2cf / a8d731be / 842bd9cb）で供給関数を呼び、before / after を設計書 §23.5 に残した。

## 一般化

短い略語は分野をまたいで多義で、単独で OR 検索に入れると網が主題外へ広がり、下流では「近い論文」の主題外として現れる。
型 upstream-loss-masked-downstream。網の広がりの判定は「すべてに当たった」だけでなく「その語だけで連れてきた」も見る。
