---
id: IK-0760
title: "論文レーダーで一般語（Full-Shape）が供給に残り網の大半を DESI の実データ解析で埋め、起点固有の主題（Galaxy Trispectrum）と古い関連論文（元の起点）が網に出ない"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-22
sources:
  - "docs/features/paper_radar_design.md §24"
  - "docs/features/paper_radar_design.md §20"
  - "docs/features/paper_radar_design.md §21"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "起点の論文の主題に近い論文を、arXiv の検索で取った網から近さで並べて教員に提示する"
  layers: [paper_radar, paper_discovery]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: low
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: arXiv の検索は新着順の先頭 N 件を取ってから近さで並べ替えるため、一般性の高い語（Full-Shape）が当たる最近の論文が網の大半を
    占めると、起点固有の主題語（Galaxy Trispectrum）を扱う論文や古い関連論文（元の起点 2407.01221・2 ループのパワースペクトル）が
    並べ替えの前に網の外へ出る。帯は全件「近い」で、この偏りは画面から見えない。処理軸 input_handling = 下流の並べ替え・帯分けの前の
    網の取り方で情報が落ちる。接続軸 condition（確信度 low: 一般語と主題語の重みが検索条件として区別されず渡っている、とも読める）。
    構造・統制は none。型は upstream-loss-masked-downstream と読んだ（網が範囲を決め、下流の帯と比較が読み替える）。網の範囲を
    「新着順の先頭」という近くの手がかりで決める点では extent-decided-by-proxy-signal にも当たる。確定には、同じ起点で一般語を外した網と
    比べ、主題語の論文・元の起点が戻るかを見る（arXiv の呼び出しは 1 回で止める）。
generalization:
  level: general
  general_form: "外部の検索で取得順（新着順）の先頭だけを網として取り、その後で関連度に並べ替えるため、一般的な検索語が網を占めると、関連は強いが取得順で後ろの対象が並べ替えの前に失われる"
pattern: upstream-loss-masked-downstream
discovery:
  perspective: [data_inspection]
  note: "浅い段の審判が scan-07 の網（9 件中 6 件の一致語が Full-Shape・Galaxy Trispectrum は当たらず）を scan-05 の網と比べ、2 ループのパワースペクトルと元の起点が網から消えていることを記録した（出所: uxsim/runs/radar_loop/judge-scan-07.md §④-4・§⑤ / judge-deep-06.md 次の周の確かめたいこと①）。"
resolution:
  perspective: [explicit_contract]
  note: "網の取り方（新着順の先頭）は arXiv の予算（§14）に触れるので変えず、網の偏りを件数なしの事実文で画面に出した。既存の下限（候補 3 件以上・有効な語 2 語以上）のもとで、「すべてに当たった」でも「単独一致が過半」でもないが取得した候補の 2/3 以上に当たる語に dominant と dominant_note（「「X」が取得した候補の多くに当たっています。網が一般語に寄っている可能性があります。」）を付け、検索結果に keyphrase_dominant_note を足す。閾値はコード定数。語の一般性の表は持たず（分野語を書かない）、外すのは教員（PD3）。JS はサーバの文を素通しし、dominant の語のチップにも §23.5 と同じ印「網を広げる」を付け、title に事実文と「クリックで外せます（外したあと「検索」で探し直します）」を添えた（外すのは既存のチップのクリック = 教員の選択・押すまで arXiv を呼ばない）。2026-10-22 のオーケストレータの対照検索（uxsim/runs/radar_loop/contrast-2026-10-22-kurtosis.json・作業ツリーを反映・arXiv 1 回）では尖度の起点で large-scale structure に既存の単独一致の印と事実文が立ち、dominant は重ねて出ないことを確かめた。"
  landed_in:
    - "backend/core/paper_discovery/radar.py"
    - "frontend/public/js/admin-paper-radar.js"
    - "backend/tests/test_paper_radar_core.py"
    - "backend/tests/test_paper_radar_ui_static.py"
    - "docs/features/paper_radar_design.md §24"
  verification:
    methods: [guardrail]
    unverified:
      - "実 arXiv で起点 2610.08908 を検索し直し、Full-Shape に dominant の事実文が出ること（arXiv は呼んでいない）"
      - "画面での表示位置と読みやすさ（ブラウザは開いていない）"
      - "網そのものの偏り（主題語の論文・元の起点が網の外に落ちる現象）は残る。取得の仕方の変更は未着手"
      - "対照検索は尖度の起点（単独一致の形）で、多くに当たる dominant の形が実データで立つかは未確認"
related: [IK-0703, IK-0706]
view_of: []
history: []
---

## 課題

症状: 供給語の是正（§21 A）後、網は宇宙論の圏内に戻ったが、Full-Shape が 9 件中 6 件の一致語になり網の大半を DESI の full-shape
実データ解析が占めた。起点の 4 点統計そのもの（Galaxy Trispectrum）を扱う論文、元の起点、2 ループのパワースペクトルは網に無い。
原因（仮説）: 新着順の先頭 N 件を取ってから近さで並べ替えるため、一般語の当たる新着が網を占める。

出所: uxsim/runs/radar_loop/judge-scan-07.md §④-4。

追加の出所（起点 2609.35757・頭脳 T2）: uxsim/runs/radar_loop/BRAIN_NOTES_T2.md（seq 3017 — 候補群が DESI 特異速度双極子・moving lens・インフレーション EFT の再和 2 件・WHIM と遠く、起点の EFT of LSS / 赤方偏移空間歪み / SPCF に当たる候補が無い。キーフレーズ「resum」「EFT」が原始揺らぎ側を引いた疑い）。判定ファイル: uxsim/runs/radar_loop/scan-08.decision.json。fixKeyphrase が別に起票する場合はそちらへ移す。

## 発見の観点

同じ起点の網を周ごと（scan-05 / scan-07）に比べ、消えた論文と一致語の偏りを突き合わせた（data_inspection）。

## 解決の観点

未解決。まず偏りを事実文で見せ、網の取り方の変更は外部 API の予算と照らしてから決める。

## 一般化

取得順の先頭で網を切ってから並べ替える構成では、網の範囲を決めている量（取得順）が判定したい量（関連度）と一致しているかを確かめる。
確かめ方は、同じ起点で一般語を外した網と比べ、主題語の論文・古い関連論文が戻るかを 1 回の呼び出しで見ること。
