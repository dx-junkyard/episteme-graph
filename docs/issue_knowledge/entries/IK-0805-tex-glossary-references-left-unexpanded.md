---
id: IK-0805
title: "TeX 取り込みで glossaries / acronym の参照（\\gls{pve}・略語マクロ \\pve）と \\xspace・\\texorpdfstring が展開されず、本文・節題・主張に $\\gls{pve}\\xspace$ が残る"
status: resolved
recorded_at: 2026-10-17
resolved_at: 2026-10-17
sources:
  - "docs/pipeline/overview.md"
feature_context:
  realizing: "TeX 原文の略語・用語集を読み、本文と節題を読める語にして後段に渡す"
  layers: [pipeline_a]
classification:
  axes:
    processing: [input_handling]
    structure: [representation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: low
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確定: tex_archive は \\newacronym / \\newglossaryentry を読まず、\\gls{…} は未知の命令として数式の目印に誤読され $\\gls{pve}\\xspace$ になった。\\texorpdfstring は 2 引数とも残り節題が「$\\gls{rsd}\\xspace$RSD」と二重になった。処理軸 input_handling = 略語の定義と参照を入力として扱っていない。構造軸 representation（low）= 略語の初出・以後という文書順の状態を持つ表現が無かった。原本 2610.08751 で \\gls 138・xspace 225 箇所 → 0。
generalization:
  level: general
  general_form: "ソース側の定義表（略語・用語集）を読まずに参照命令だけを汎用規則で処理し、参照が残骸として後段・LLM 入力に流れる"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 U1 が document 1471ff7f（2610.08751）の paper_skeleton（seq 3019）と rhetorical_role（seq 3025・3088・3094）・claim_qualification（seq 3118）の材料を読み、\\gls{pve}\\xspace の未展開と節題の二重表記を見た。出所: uxsim/runs/radar_loop/BRAIN_NOTES_U1.md。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: "_extract_glossary が \\newacronym / \\newabbreviation / \\newglossaryentry / acronym・acro 系の定義を読み、_resolve_glossary が本文を文書順に走査して初出はフル表記 (略語)・以後は略語に置く（複数形・大文字化・挿入句・\\glsdisp 対応、節題と caption は略語のみで初出を消費しない、定義が無ければ key を残す）。略語へ展開される論文マクロ（\\pve）は先に開く。\\texorpdfstring は TeX 側を残し \\xspace は除く。題名にも同じ処理。"
  landed_in:
    - "backend/core/document_pipeline/tex_archive.py"
    - "backend/tests/test_latex_display.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: extracted
      note: "本文の取り込み入口 1 箇所（_tex_to_blocks_and_sections）と題名の 1 箇所で解決し、後段の整形は略語命令を見ない形にした。設計前に原理は読んでいない。"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "\\gls を数式の中で使う論文（略語に $ を含むとき）の表示"
related: [IK-0696, IK-0804]
view_of: []
history: []
---

## 課題

症状: 本文に `$\gls{pve}\xspace$`、節題に `Comparison between different $\gls{rsd}\xspace$RSD models`、主張の atomic 本文に \gls が残る。

原因: 略語の定義を読まず、参照命令を汎用規則で処理していた。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_U1.md（seq 3019・3025・3088・3094・3106・3118）。

是正前の出所（2610.08751・頭脳 W0）: uxsim/runs/radar_loop/BRAIN_NOTES_W0.md（seq 3138・3144・3147・3159・3207 — \gls{…} が未展開で略語の正式名が材料に無い。seq 3213・3228 — 節題で \gls 残骸の直後に展開済みの本体が重複（$\gls{rsd}\xspace$RSD・$$\Lambda$CDM\xspace$ΛCDM）。seq 3243 — 節題だけ略語が展開される非対称）。TeX マクロ是正の前に取り込んだ素材。

是正前の出所（2610.08751・頭脳 W1）: uxsim/runs/radar_loop/BRAIN_NOTES_W1.md（seq 3154・3163・3175 — \gls{…} が未展開で略語の正式名が材料に無い。seq 3214・3229 — 節題の二重表記（$\gls{rsd}\xspace$RSD・$$\Lambda$CDM\xspace$ΛCDM）。seq 3244 — 節題だけ展開される非対称）。TeX マクロ是正の前に取り込んだ素材。

是正前の出所（2610.08751・頭脳 W2）: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3137 — headline_claim に「$\gls{pve}\xspace$」。seq 3155・3158 — \gls{fog}・\gls{apdist} の展開語（Fingers-of-God・Alcock-Paczynski）が材料に無く頭脳が事前知識で展開した。seq 3212・3230 — 節題の二重表記）。TeX マクロ是正の前に取り込んだ素材。

是正前の出所（頭脳 X1・是正前の run（seq 3289〜3310・2026-10-17 03:00〜03:11））: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3295 — 記号定義文に「$$w$CDM\xspace$」の二重表記と \xspace 残骸。seq 3304 — scope_candidates の主張本文に `$\gls{bao}\xspace$`）。頭脳は seq 3340（是正後の run）でブロック本文が「the BAO feature」と展開済みなのを見て「経路で展開が違う」と読んだが、req の時刻で判別すると 3304 は是正前の run・3340 は是正後の run で、違いは経路ではなく run（是正の前後）による。

残る観察（第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3582 — FoG 論文で SPCF が略語だけで、正式名が材料に無い。略語の定義が glossaries 以外の形（本文の地の文での定義・独自マクロ）の論文では、\gls の展開の是正が届かない）。

## 発見の観点

LLM 入力の本文と TeX 原文のプリアンブルを突き合わせた（data_inspection）。

## 解決の観点

定義表を読んで文書順に解決する単一の入口を置いた。

実機の再現確認（reproduction_rerun）: 2026-10-17 03:49 に 2610.08751（document 1471ff7f）を是正入りの api-server で全再解析（run 40ecf8df）し、再パース後の chunks.text の全チャンクで `\gls{` と `xspace` の残骸ゼロを確かめた（是正前の同じ論文には fixTexMacros の計測で多数あった）。

解消の確認（reproduction_rerun・頭脳 X0・全是正入りの再解析 run 40ecf8df（seq 3315 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3336・3342・3408 — \gls の残骸が消えて EFT・VDG・AP が正式名つきで展開される。seq 3384 — 節題の略語の重複も消えた）。

解消の確認（reproduction_rerun・頭脳 X2・再解析 run 40ecf8df（seq 3317 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3317 — 「prior-volume effects (PVE)」「redshift-space distortions (RSD)」と略語が展開され、\gls の生マクロが消えた）。

解消の確認（reproduction_rerun・頭脳 Y1・再解析 run 40ecf8df）: uxsim/runs/radar_loop/BRAIN_NOTES_Y1.md（seq 3409・3436 — 本文と脚注で BAO / FS が展開済みで、\gls{bao}\xspace の残骸は出ない）。残る観察（頭脳 Y0 seq 3429・3480・3501）: 略語の正式名は初出のブロックにだけ出るため、後の span 単体では PVE・TCM・RSD が略語のままで、span 単位の材料としては正式名が届かない（LaTeX の \gls の初出展開の挙動どおり）。

残る観察（頭脳 Z2・2609.35757 の再解析 run 54e85a2a と 2610.08751 の再解析 run の後段）: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3671 — discuss_opening の untested_assumptions の材料に「$\gls{pve}\xspace$」の生マクロが残り、文が「for ongoing.」で途切れる。同じ論文の再解析後の chunks では残骸ゼロを確かめているので、台帳・主張の行に旧 run の本文が残っている疑い（未確認）。seq 3584・3611・3668 — SPCF・HOD の正式名がこの窓に無い（初出の定義が窓の外、または glossaries 以外の形））。

## 一般化

定義表を読まない参照は残骸になる。辞書の型 information-dropped-as-unrepresentable の近傍。

