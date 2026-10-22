---
id: IK-0799
title: "TeX 経路の論文で paper_skeleton の Section Structure が上位 7 節だけで下位節が欠け、全節が「(page 1)」と表示される"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-17
sources:
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "論文の節構成を添えて、論文の背骨（目的・主結果・節の役割）を LLM に仮説化させる"
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: low
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確定（2026-10-17）: paper_skeleton の input_builder が節構成を上位レベルの節だけで組み、各節に頁を添える書式を経路を問わず使う。TeX 経路は頁を
    持たないので全節が既定の page 1 になる（TeX 取り込みの節自体は level と section_number を持つ）。接続軸 information = 下位節の構成が材料に渡らない。構造軸 representation（low）= 頁が無い
    経路を表す値が無く、既定値が実在の頁に見える。処理軸 none / 統制軸 none。
generalization:
  level: general
  general_form: "経路ごとに持たない属性を既定値で埋めたまま共通の書式で出すため、既定値が実在の値として読まれ、絞り込みで下位の構造も落ちる"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 T0 が再取り込みの document 2a24b9e4 の paper_skeleton（seq 2973）の材料を読み、Section Structure が上位 7 節だけ・全節 page 1・要旨と代表ブロックの途中切り詰めに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md）。軽い所見。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: "原因の確定（2026-10-17・頭脳 U1 seq 3019 / 3034 の 2610.08751 で再確認）: TeX 取り込みの節は level と section_number を既に持っていた（tex_archive）。下位節を落としていたのは paper_skeleton の _section_headers が level-1 節だけを渡すこと、page 1 は頁を持たない経路でも page_start を出す書式。是正: level-1 節の各見出しに下位節を subsections として入れ子で添え（節の母集合・coverage・件数は level-1 のまま）、section_number を付け、metadata.pages が 0 の経路では page_start を None にしてプロンプトが「(page N)」を出さない。代表ブロックは下位節の本文も含め、表本体（raw.in_table）は代表・結論に使わない。要旨・代表ブロックの途中切り詰めは未変更。"
  landed_in:
    - "src/episteme_graph/agents/paper_skeleton/input_builder.py"
    - "src/episteme_graph/agents/paper_skeleton/prompt.py"
    - "backend/tests/test_latex_display.py"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "実 LLM の paper_skeleton で backbone の節役割が下位節に対応するか"
      - "要旨・代表ブロックの途中切り詰め（未是正）"
related: [IK-0332, IK-0101]
view_of: []
history: []
---

## 課題

症状: TeX 経路の論文の paper_skeleton の Section Structure が上位 7 節だけで下位節が欠け、全節が「(page 1)」。要旨と代表ブロックも途中で切れる。
原因（仮説）: 節一覧が上位レベルに絞られ、頁を持たない経路でも頁の既定値を表示している。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2973）。

追加の出所（頭脳 U1）: uxsim/runs/radar_loop/BRAIN_NOTES_U1.md（seq 3019 — 小節が節一覧に出ず本文の Sec. 番号と sec_N が対応しない・seq 3034 — 小節 sec_3 の backbone_block_type が null）。

追加の出所（2610.08751・頭脳 U1）: uxsim/runs/radar_loop/BRAIN_NOTES_U1.md（seq 3019 — 小節が paper_skeleton の節一覧に出ず、本文の「Sec. N」番号と sec_N が対応しない。seq 3034 — 節一覧に無い小節 sec_3 のブロックには backbone_block_type が渡らない（null））。

追加の出所（頭脳 W0）: uxsim/runs/radar_loop/BRAIN_NOTES_W0.md（seq 3264 — 本文の節参照「Sect. Sec. 2.1 / 2.2 / 4」と内部の section_id（sec_3=Formalism 等）の番号体系が食い違い、印字節番号と sec_N の対応表が材料に無い）。

追加の出所（頭脳 X1・全是正入りの再解析 run 40ecf8df（seq 3313 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3313 — 再解析の paper_skeleton でも代表ブロックが 1 節あたり 1〜2・各 500 字で切れ、Formalism（sec_3）の本文ブロックが 1 つも無い。節題・本文の ${\rm Mpc}/h$ が生 TeX のまま（seq 3367 も section_title で同じ））。

追加の出所（頭脳 X0・全是正入りの再解析 run 40ecf8df（seq 3315 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3396 — 付録ブロックに backbone_block_type 行が無い。seq 3324 — 節の道案内のブロック（section_meta）に backbone_block_type=derivation が付く。seq 3315 — 要旨ブロックの backbone 種別が無い。seq 3327 — 内部の sec_3 と本文の Sect. 2.1 の対応のずれ）。

追加の出所（頭脳 X2・再解析 run 40ecf8df（seq 3317 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3395 — 付録ブロックに backbone_block_type 行が無い。seq 3335 — sec_3「Formalism」が本文の「Sect. 2.2」と番号がずれる）。

残存の確認（頭脳 Y0/Y1/Y2・再解析 run 40ecf8df）（この是正がその run の取り込みより後に入ったかは照合していない）: uxsim/runs/radar_loop/BRAIN_NOTES_Y0.md（seq 3513・3510）、uxsim/runs/radar_loop/BRAIN_NOTES_Y1.md（seq 3508・3512 — 付録ブロックの backbone_block_type 行が claim_qualification でも欠ける。印字節 4 が内部 sec_7 を指す対応が材料に無い。seq 3409 — backbone_block_type=problem_setting がブロック単位で付き、先行研究の span にも付く）。

残存の確認（第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3576・3582 — section_id は sec_3 / sec_4 だが印字節は 2.x で、印字節番号と sec_N の対応表が材料に無い。seq 3582 — 導出の帰結のブロックに backbone_block_type=background が付く）。

残存の確認（第 2 波 deploy 後の run・頭脳 Z0/Z1）: uxsim/runs/radar_loop/BRAIN_NOTES_Z1.md（seq 3619・3694 — 本文の「Section 4」「We end this section」と section_id（通し番号の sec_N・小節単位の区切り）が食い違う）、uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3666・3672 — 付録節の backbone_block_type が一律 result。seq 3693 — 小節境界に「from the next section」の注記が付く）。

残る観察（頭脳 AA2・2609.35757 の再解析 run 54e85a2a）: uxsim/runs/radar_loop/BRAIN_NOTES_AA2.md（seq 3791 — 本文の「In Section 3」が指す節は sec_6 で、節番号から sec_N への対応が材料に無い（小節も sec_N の通し番号））。

## 発見の観点

paper_skeleton の節一覧を文書構造の節（下位節を含む）と突き合わせた（data_inspection）。

## 解決の観点

節一覧の組み立て（paper_skeleton）で下位節を入れ子に添え、頁を持たない経路では頁を出さない（単一点の是正 + 回帰テスト）。

解消の確認（reproduction_rerun・頭脳 Z2・2609.35757 の再解析 run 54e85a2a と 2610.08751 の再解析 run の後段）: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3701〜3719 — 2609.35757 の付録（sec_18）にも backbone_block_type が付く）。残る観察: seq 3665 — 小節の section_title が「Formalism」だけで親節（Estimates from the Halo Model）が付かず、別論文の sec_3「Formalism」と同名になる。seq 3662 — 節見出しの導入段落 1 ブロックだけが別の section になる。seq 3650・3662 — 本文の「Section 3 / 4」と sec_N の番号が別体系。seq 3665・3695 — 導入句・結論の背景段落に result / core_relation が節単位で付く。

## 一般化

持たない属性を既定値で出すと実在の値に見える。辞書の型 information-dropped-as-unrepresentable の近傍。
