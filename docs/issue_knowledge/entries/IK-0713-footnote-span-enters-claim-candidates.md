---
id: IK-0713
title: "TeX の脚注本文が「（脚注: …）」だけの span として切り出され、本文の主張と同じ母集合で主張の採否に回る"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-17
sources:
  - "docs/features/url_material_upload_design.md"
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "TeX ソースから取り込んだ本文を役割判定・主張採否に回し、論文本体の主張だけを claim 候補にする"
  layers: [pipeline_a, url_material_fetch]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（IK-0698 の是正後の現物）: TeX 取り込みは \footnote{…} を段落内の括弧書き「（脚注: …）」で区切るだけで、脚注を本文と別の
    ブロック・別の種別として表さない。役割判定はその括弧書きを独立 span として切り出し、主張採否は他の span と同じ扱いで受ける。
    構造軸 representation = 「脚注である」ことを表すブロック種別・フラグが無い。接続軸 information = 脚注という性質が取り込みから
    後段に届かない。処理軸 none = 括弧書きでの区切り自体は意図どおり。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "付記を本文と同じ単位の中に区切りだけで残すと、後段は付記と本文を区別できず、本文向けの判定の母集合に付記が混ざる"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回の頭脳メモ uxsim/runs/radar_loop/BRAIN_NOTES_I1.md（seq 1609）が、claim_qualification の入力の Next Span が「（脚注: Alternately, the null combination …）」だけであることを見た（uxsim/runs/mailbox/req-01609.json）。"
resolution:
  perspective: [single_point_fix]
  note: "TeX 取り込みのブロック確定（add_block）1 箇所で、「（脚注: …）」を書かれた位置から所属する文の終わり（略語・数式内の句点は見ない。判定できなければブロック末尾）へ移し、本文の 1 文を割らないようにした。脚注の本文は raw.footnotes にも分けて残す（本文テキストからは消さない = P4）。原本 4 本で文中に挟まった脚注 5 → 0、raw.footnotes は 12 / 5 / 1 / 5 件。後段（rhetorical_role / claim_qualification）が raw.footnotes を使って脚注を母集合から外す変更は入れていない（別エージェントの編集中の領域）。"
  landed_in:
    - "backend/core/document_pipeline/tex_archive.py"
    - "backend/tests/test_latex_display.py"
    - "backend/tests/test_document_pipeline.py"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "文末に書かれた脚注（謝辞の脚注・seq 2804）は文末に残るので、役割判定が独立 span にする挙動は変わらない — raw.footnotes を後段が読む変更が要る"
      - "パイプラインの再解析（DB 上の run）は行っていない — 原本 4 本の取り込みをローカルで読み直したのみ"
related: [IK-0698, IK-0712]
view_of: []
history: []
---

## 課題

症状: 脚注の本文だけの span が主張採否の母集合に入り、本文の主張と同じ判定を受ける。観察の出所は uxsim/runs/radar_loop/BRAIN_NOTES_I1.md（seq 1609）。

原因: IK-0698 の是正で脚注は括弧書きで境界を保つようになったが、ブロックの種別としては本文のまま。後段には「脚注である」ことが届かない。

追加の出所（2609.35757・TeX 経路）: uxsim/runs/radar_loop/BRAIN_NOTES_S1.md（seq 2797 — Source Block に脚注が連結）、uxsim/runs/radar_loop/BRAIN_NOTES_S2.md（seq 2783・2798 — Next Span に「（脚注: …）」が連結、seq 2804 — 謝辞の脚注「（脚注: We thank …）」だけが独立の Next Span として切り出され、主張採否の母集合に入り得る）。

追加の出所（頭脳 S0）: uxsim/runs/radar_loop/BRAIN_NOTES_S0.md（seq 2784・2799 — 「（脚注: …）」が span の text に混入し命題化される / seq 2805 — 謝辞の脚注が単独 span）。

再発確認（頭脳 T2・是正後の run）: uxsim/runs/radar_loop/BRAIN_NOTES_T2.md（seq 2993 — 再取り込みの document 2a24b9e4 でも脚注「（脚注: …）」が本文に連結したまま。是正前の c795b2cf の seq 2876・2879（謝辞の脚注が本文末尾に連結）と同じ症状）。

再発確認（2610.08751・頭脳 U1）: uxsim/runs/radar_loop/BRAIN_NOTES_U1.md（seq 3049・3052・3079 — 脚注が「（脚注: …）」で本文に連結され、脚注由来の span が本文の主張と同列で候補化される）。

追加の出所（頭脳 W0）: uxsim/runs/radar_loop/BRAIN_NOTES_W0.md（seq 3141・3153・3270 — 文中の脚注が「（脚注: …）」で挿入されて本文の 1 文を前後の span に割り、Next Span が「 leading to」だけの断片になる。seq 3156 — 文末の脚注は独立 span になり、扱いが位置で揺れる）。

追加の出所（頭脳 W1）: uxsim/runs/radar_loop/BRAIN_NOTES_W1.md（seq 3190 — 脚注本文がダッシュ節の内側に挿入されて本文と 1 span に混ざる。seq 3145・3157 では脚注が別 span で、扱いが位置で揺れる）。

追加の出所（頭脳 W2）: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3140・3269 — 脚注が文の途中に挿入され、本文の続きが「 leading to」の 2 語の span に孤立する。seq 3161 — 脚注の span に「（脚注:」の印が無く本文か脚注か判別できない）。

再発確認（頭脳 X1・全是正入りの再解析 run 40ecf8df（seq 3313 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3322・3328 — 脚注が文の途中（「$w_0w_a$CDM（脚注: …）cosmologies」「F,（脚注: …） leading to」）に挿入され、主張の文が分断される。seq 3385 — Previous Context が脚注だけで直前本文の主張が見えない）。

未是正の確認（頭脳 X0・全是正入りの再解析 run 40ecf8df（seq 3315 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3366 — 脚注が文の途中「σ12（脚注: …）---with the baseline…」に挿入され、1 文を割らずに span を取るには脚注ごと含めるしかない。seq 3342・3375 — Previous Context に脚注が挿入され、窓の先頭が脚注の閉じ括弧から始まる）。

追加の出所（頭脳 X2・再解析 run 40ecf8df（seq 3317 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3341 — 脚注の印を含めずに span を切ると、下流で脚注由来と分からなくなる。seq 3437 — 逆に「（脚注: …）」の印ごと span になり主張本文に残る。脚注の印が span に入るかが切り方次第で揺れる。seq 3356 — Previous Context の窓の切り口が脚注の中）。

残存の確認（頭脳 Y0/Y1/Y2・再解析 run 40ecf8df）（この是正がその run の取り込みより後に入ったかは照合していない）: uxsim/runs/radar_loop/BRAIN_NOTES_Y1.md（seq 3427 — 脚注が「F,（脚注: …） leading to」と文中に挿入され、括弧書きごと主張本文になる。seq 3442・3443 — Next Span の脚注から「（脚注: …）」の印が剥がれ本文の次文として渡る。seq 3470 — σ12 と ---with の間に脚注が埋まる）、uxsim/runs/radar_loop/BRAIN_NOTES_Y0.md（seq 3444 — 脚注の span が本文の主張と区別されない）。

残存の確認（第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3588 — tex_b64 span_002 は「（脚注: …）」付き、tex_b66 span_002 は脚注本文なのに印なしで、脚注の印が span によって落ちる）。

残存の確認（第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3591 — candidate span tex_b71 span_002 は脚注本文なのに「（脚注:」の印が無い（3588 の再現）。seq 3687 — 脚注が文の途中（ansatz is の直後・式の前）に挿入され式の導入を割る）。

解消の確認（頭脳 AA2・2609.35757 の再解析 run 54e85a2a）: uxsim/runs/radar_loop/BRAIN_NOTES_AA2.md（脚注は「（脚注: …）」の印付きの独立 span として渡り、本文の文を割らない観察が続く）。

## 発見の観点

主張採否の入力を実データで読んだ（data_inspection）。

## 解決の観点

2026-10-17: 別ブロックにはせず（IK-0698 の判断を維持）、脚注を所属する文の終わりへ移して本文の文を連続させ、脚注の本文を raw.footnotes に分けて持たせた（後段が母集合から外すための材料）。後段の除外は未着手。

解消の確認（頭脳 Z2・2609.35757 の再解析 run 54e85a2a と 2610.08751 の再解析 run の後段）: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3680 — 脚注が「（脚注: …）」の印付きで文の間に置かれ、span として境界が取れる）。残る観察: seq 3596 — Previous Text Blocks の tex_b71 には「（脚注:」があるが candidate_text_spans の span_002 には印が無い非対称。seq 3656 — 窓の末尾を取る方式で脚注が前ブロックの本文を押し出し、「the above discussion」の中身が見えない。

## 一般化

付記を区切りだけで本文に残すと、本文向けの判定に付記が混ざる。確かめ方は、脚注を含む TeX を通し、主張候補に脚注だけの span が現れないこと（かつ脚注の本文が失われていないこと）を見る。辞書の型 information-dropped-as-unrepresentable。
