---
id: IK-0759
title: "論文レーダーの取り込み済み候補のカードが、取り込み前の AI 比較で読めていた関係の種類（同じ対象を扱う等）を言わず、取り込むと「何の関係か」の言葉が画面から消える"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/features/paper_radar_design.md §21"
  - "docs/features/paper_radar_design.md §19"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "教員が近い論文を取り込んだあとも、起点の論文との関係の種類（同じ対象・隣り合う話題など）を研究判断に使える形で読む"
  layers: [paper_radar]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: AI 比較は取り込み済みの候補を対象から外し（skip: ingested）、比較結果は保存しない（PR4 非保存）ため、取り込み前に 2 回
    読めていた関係の種類（shared_subject = カウンター項を使う側と起源を調べる側）が取り込み後のカードに届かない。取り込み後に使える
    材料（配置の対比・構造照合・論文層）は §21 で配線されたが、「どの面で重なるか」は言えても「何の関係か」を言う材料がどれにも無い。
    接続軸 information = 関係の種類を言える材料（比較の 1 コール）は既にあるが、取り込み済みカードへ渡る経路が無い。処理・構造・統制は
    none（比較の判定そのもの・語彙・弁は足りている）。確定には、取り込み済み候補が比較の選択肢から外れる条件と、カードの DTO に
    関係の種類のキーが無いことをコードで確かめる。
generalization:
  level: general
  general_form: "対象の状態が変わる操作（取り込み・承認など）の前にだけ動く判定の結果が保存されず、状態が変わった後の面ではその判定を呼ぶ経路も無いため、操作前に読めていた言葉が操作後に消える"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: "浅い段の審判が scan-06・deep-06 の比較（2 回とも shared_subject）と scan-07 の取り込み済みカード（skip: ingested・関係の種類なし）を並べ、取り込みの前後で関係の言葉が消えることを記録した（出所: uxsim/runs/radar_loop/judge-scan-07.md §④-3 / judge-deep-06.md）。"
resolution:
  perspective: [pending]
  note: "未解決。比較を保存するのは PR4 に触れるため採らない。候補: ①取り込み済み候補も「比較する」の対象に選べるようにする（既存の 1 コール・日次上限に相乗り）②配置の対比の近くに「関係の種類は比較で確かめられます」の導線 1 行を添える。"
  landed_in:
    - "docs/features/paper_radar_design.md §21"
related: [IK-0750]
view_of: []
history: []
---

## 課題

症状: 取り込み前の AI 比較で 2 回とも「同じ対象を扱う」と読めていた候補（Kinetic Theory of the EFTofLSS）が、取り込み後のカードでは
配置の対比・構造照合の導線だけになり、起点との関係の種類を言わない。
原因（仮説）: 比較は取り込み済みを対象外にし、結果を保存しない（PR4）。取り込み後の材料に関係の種類を言うものが無い。

出所: uxsim/runs/radar_loop/judge-scan-07.md §④-3。

## 発見の観点

同じ候補の取り込み前（scan-06・deep-06 の比較）と取り込み後（scan-07 のカード）の記録を並べた（data_inspection）。

## 解決の観点

未解決。非保存（PR4）を保ったまま、既存の比較を取り込み済み候補にも呼べるようにする配線を第一候補とする。

## 一般化

状態が変わる操作の前にだけ動く判定は、操作後の面からも呼べるかを決めておく。確かめ方は、検索 → 取り込み → 取り込み済みカードの
操作列を辿り、関係の種類がカードから読めること（または比較を呼べること）を画面で見ること。
