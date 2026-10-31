---
id: IK-0818
title: "論文レーダーの AI 比較が要旨 2 本だけを材料にし、取り込み済み候補の解析結果（中心命題・支持構造・本文の主張）を使わないため、「VDG の減衰関数は FoG の模型」のような決定的な接点を共通点に挙げられず shared_subject 止まりになる"
status: resolved
recorded_at: 2026-10-17
resolved_at: 2026-10-17
sources:
  - "docs/features/paper_radar_design.md"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "起点の論文と候補の論文の関係（共通点・違い）を、逐語の根拠付きで 1 回の AI 比較にまとめる"
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
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: compare の入力が起点・候補とも arXiv の要旨（と起点の承認済み部品ラベル）だけで、取り込み済み候補ではこちらで解析した中心命題・
    支持構造・本文の主張があっても材料に入らない。決定的な接点（候補の VDG∞ が FoG の減衰模型であること・FoG の係数の単位依存）は本文の
    主張と支持構造にはあるが要旨には無いので、逐語検査付きの比較では共通点に挙げられない。接続軸 information = 解析済みの情報が比較の
    材料に届かない。処理軸 none / 構造軸 none / 統制軸 none。取り込み前の候補には解析結果が無いので、取り込み済みカードの比較に限る。
    確定には compare の input_builder と、取り込み済み候補で document を引けるかを読む。主張がグラフのノードに結ばれていない件（同じ深い段の
    所見）は別の原因。
generalization:
  level: general
  general_form: "比較の材料を外部の要約だけに固定するため、こちらで解析済みの詳しい情報があっても使われず、決定的な接点が比較に出ない"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 X0 が discovery:compare（起点 = FoG 再和論文・候補 2610.08751・seq 3306）を読み、関係は要旨の「EFT」「赤方偏移サーベイの銀河クラスタリング」で読めるが、VDG∞ が FoG の減衰模型という具体の重なりは要旨に無く言えないことに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md）。深い段の審判も同じ所見を改善の仮説 1 に挙げた（uxsim/runs/radar_loop/judge-deep-09.md ④-1 — 本文の主張としてはあるのに、グラフ・配置・比較のどこにも結ばれていない）。"
resolution:
  perspective: [carry_through, required_argument]
  note: "取り込み済み候補（閲覧者に見える document あり）では、候補の承認済み部品ラベル（無ければ承認前を明示）と中心命題を同じ 1 コールの比較材料に足した。可視集合は route から必須の注入で受け、空なら要旨だけ（fail-closed）。重なりの部品名は起点 ∪ 候補のリストで照合して側（seed / candidate / both）をサーバが導出し、引用は要旨か中心命題の逐語で可として出所を返す。LLM 回数・CostGate・非保存・caveat は不変。"
  landed_in:
    - "backend/core/paper_discovery/compare.py"
    - "backend/api/routes/paper_discovery.py"
    - "frontend/public/js/admin-paper-radar.js"
    - "backend/tests/test_paper_radar_core.py"
    - "docs/features/paper_radar_design.md"
  principles:
    - principle: enforce-condition-in-query-with-required-argument
      use: consulted
      note: >-
        採った部品は可視集合の注入と空での SQL 非発行（既存 visible_ingested_documents の再利用）。固有の差分: 比較は補助材料なので、読めないときは閉じたうえで要旨の比較を止めない（fail-soft と fail-closed の併用）。
  verification:
    methods: [guardrail]
    unverified:
      - "実 LLM で 2610.08751 との比較を再演し、VDG の減衰関数と FoG の接点が overlaps に候補側の出所付きで出ること"
      - "支持構造・本文の主張だけにある接点（中心命題・部品名に現れないもの）は渡していないので拾えない"
related: [IK-0708, IK-0709, IK-0759]
view_of: []
history: []
---

## 課題

症状: 起点（FoG 再和論文）と取り込み済み候補 2610.08751 の AI 比較が shared_subject（同じ対象を扱う）止まりで、両者を結ぶ決定的な接点
（候補の VDG∞ の減衰関数が FoG の模型であること・FoG の係数の単位依存）が共通点に出ない。その接点は候補の本文の主張・支持構造には
あるが要旨には無い。
原因（仮説）: 比較の材料が要旨だけで、取り込み済み候補の解析結果を使わない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3306）、uxsim/runs/radar_loop/judge-deep-09.md（④-1）。

## 発見の観点

比較の材料（要旨）と、候補の解析済みの主張・支持構造を突き合わせた（data_inspection）。

## 解決の観点

段階間で落ちていた解析結果を比較の段まで運び（carry_through）、可視性は必須の注入で受ける（required_argument）。

## 一般化

比較の材料を外部の要約に固定すると、手元の詳しい情報が使われない。辞書の型 available-but-unwired。
