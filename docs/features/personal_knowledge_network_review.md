# 個人知識ネットワーク実装レビュー

対象設計書: `docs/features/personal_knowledge_network_design.md`  
レビュー日: 2026-07-15  
方針: 実装の変更は行わず、設計書との整合性のみを確認した。

## 総評

本人スコープの読み取り API、N2/N3/N4 の基本導出、confirmed の同一性リンクだけを読む意図、数値を表示しない UI は、概ね設計に沿っている。

一方で、PN-3（LLM 候補を根拠にしない）、PN-7（閲覧不可 document への hop を省く）、および L層の active-only 規則に関わる差分がある。現状を「設計書どおりに実装済み」と判定することはできない。

## 指摘事項

### P1: 未接続 tension が LLM 候補の component をアンカーに使う

設計書 §2 は、`connect` 済みの tension だけが `payload.target_refs.component_ids / edge_ids` をアンカーに使い、未接続 tension は topic 粒度へ縮退すると定めている。

しかし [`backend/core/personal_graph/derive.py`](../../backend/core/personal_graph/derive.py) の `_tension_anchor` は status を判定せず、`target_refs.component_ids` があれば component アンカーを選ぶ。tension 候補はもともと LLM が `target_refs` を持ち、[`backend/api/services.py`](../../backend/api/services.py) の `confirm_tension_trace` は confirm 時にその payload を保持したまま status だけを `open` / `articulated` に変える。

そのため、本人が tension 自体を引き受けただけで、本人が接続操作をしていない LLM 帰属の component が個人ネットワークと journey の根拠になる。これは N1 のアンカー規則と PN-3 に反する。

### P1: journey が起点 document の閲覧可否を確認しない

設計書 §6 と PN-7 は、学習者が閲覧できない document への hop を黙って省くよう求めている。

[`backend/core/personal_graph/journey.py`](../../backend/core/personal_graph/journey.py) の `journey_for_node` は、component / claim から document を解決すると、コース内 document 集合を取得する前に、その document のローカルグラフと confirmed identity link を読む。コース内集合は [3] の他教材を絞り込む用途にしか使われない。

加えて、[`backend/api/services.py`](../../backend/api/services.py) の `connect_tension_trace` は、渡された component ID が当該コースで閲覧可能かを検証しない。このため不正な component 参照を持つ trace から、閲覧不可 document の構成名や confirmed link を journey が返し得る。

### P2: retired な library entry を経由して traversal できる

設計書 §6 / §10 は、L層ハブとして `library_entries` の active 行だけを使うよう定めている。

[`backend/core/personal_graph/queries.py`](../../backend/core/personal_graph/queries.py) の `fetch_library_entry_names` は active entry のみを返すが、[`backend/core/personal_graph/journey.py`](../../backend/core/personal_graph/journey.py) は名前を取得できなかった shared part も処理する。さらに `fetch_confirmed_links_for_shared_part` から他 document を集めるため、retired entry であっても generic な「共通部品」として [2] / [3] を辿れる。

active entry であることを traversal の前提条件として扱えていない。

### P2: コース切替中の非同期応答が別コースの UI に表示される

設計書 §1 は個人ネットワークを `(user_id, course_id)` 単位と定め、P-1 実装ノートもコース切替時の invalidate を要求している。

[`frontend/public/js/personal-map.js`](../../frontend/public/js/personal-map.js) では、`loadNetwork(courseId)` と `fetchJourney(courseId, nodeId)` の完了時に、要求開始時の course ID が現在の course ID と一致するか確認しない。`invalidate()` はキャッシュと表示状態を消すが、進行中の Promise は無効化しない。

したがって、コース A のネットワークまたは旅を要求した直後にコース B へ切り替えると、遅れて到着した A の応答が B のトレイまたは旅カードに描画される可能性がある。

## 検証状況

静的な設計・実装照合を実施した。関連 pytest はローカル環境に pytest がなく、Docker による実行も安全承認で許可されなかったため、実行していない。
