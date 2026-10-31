"""教材行の参照健全性チップ（`_material_reference_health`）の投影規律。

2026-09-19 再現性レビュー E-11: 旧文言「参照の切れはありません。」の保存済み
スナップショットは検査対象が狭かった頃の記録なので、チップは「未確認」として出す
（旧 ok を根拠に壊れていないと読ませない）。
"""
from __future__ import annotations

from core import reference_health as rh


def _project(snapshot):
    from routes.admin import _material_reference_health

    return _material_reference_health(snapshot)


def test_legacy_ok_snapshot_is_projected_as_unchecked():
    snap = {"reference_health": {"status": rh.STATUS_OK, "checked_at": "2026-09-15T00:00:00Z", "facts": [rh.LEGACY_FACT_OK]}}
    assert _project(snap) == {"status": rh.STATUS_UNCHECKED}


def test_current_ok_snapshot_keeps_status_and_checked_at():
    snap = {"reference_health": {"status": rh.STATUS_OK, "checked_at": "2026-09-19T00:00:00Z", "facts": [rh.FACT_OK]}}
    assert _project(snap) == {"status": rh.STATUS_OK, "checked_at": "2026-09-19T00:00:00Z"}


def test_missing_snapshot_is_unchecked():
    assert _project({}) == {"status": rh.STATUS_UNCHECKED}
    assert _project(None) == {"status": rh.STATUS_UNCHECKED}
