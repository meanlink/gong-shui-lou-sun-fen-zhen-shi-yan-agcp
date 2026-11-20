"""听漏仪停用演练：冻结原试验样本，按共用声学区间逐段穷举结论。"""
import time

from fastapi import HTTPException
from sqlalchemy import select

from src.models import Dataset, Drill, Job
from src.services.business import (
    DRILL_MAX_DB,
    drill_brief,
    drill_snapshot,
    drill_summary,
    drill_table,
    drill_verdict,
)


def _interval(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(422, f"{label}须为数值。") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise HTTPException(422, f"{label}须为有限数值。")
    if not 0 <= number <= DRILL_MAX_DB:
        raise HTTPException(422, f"{label}须在 0~{DRILL_MAX_DB:g} dB 之间。")
    return round(number, 2)


def normalize_interval(lo, hi):
    low = _interval(lo, "区间下限")
    high = _interval(hi, "区间上限")
    if low > high:
        raise HTTPException(422, "区间下限不得高于上限。")
    return low, high


def _build(db, job_id, name, lo, hi, user, previous=None):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "分析任务不存在")
    if job.status != "completed" or not job.result:
        raise HTTPException(409, "仅已完成的规则试验可发起停用演练")
    dataset = db.get(Dataset, job.dataset_id)
    if not dataset:
        raise HTTPException(404, "原试验的数据批次不存在")
    low, high = normalize_interval(lo, hi)
    if previous is not None:
        # 改区间沿用同一份冻结样本：迟到记录不得混入新版演练。
        snapshot = previous.snapshot or {}
        if not snapshot.get("validation") or not snapshot.get("tree"):
            raise HTTPException(409, "原演练缺少冻结样本，无法改区间")
    else:
        try:
            snapshot = drill_snapshot(job, dataset)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    drill = Drill(
        name=name.strip(),
        job_id=job.id,
        dataset_id=job.dataset_id,
        lo=low,
        hi=high,
        status="queued",
        snapshot=snapshot,
        snapshot_digest=snapshot["snapshot_digest"],
        source_result_checksum=snapshot["source_result_checksum"],
        result={"rows": [], "interval": {"lo": low, "hi": high}, "snapshot_digest": snapshot["snapshot_digest"]},
        error="",
        complete=False,
        attempts=0,
        approved=False,
        created_by=user.id,
        created_at=time.time(),
        revision=int(previous.revision) + 1 if previous is not None else 1,
        previous_id=previous.id if previous is not None else None,
    )
    db.add(drill)
    db.flush()
    if previous is not None:
        previous.superseded_by = drill.id
        # 旧区间不得再批准：撤回尚未冻结的批准，只留审计记录。
        previous.approved = False
        previous.approved_result = {}
        previous.approved_by = ""
        previous.approved_at = None
    return drill


def _active_for_job(db, job_id):
    return db.scalar(
        select(Drill)
        .where(Drill.job_id == job_id, Drill.superseded_by.is_(None))
        .order_by(Drill.id.desc())
    )


def create(db, data, user):
    """一次试验只保留一个有效演练：重新填区间即取代旧版本，旧批准随之撤回。"""
    previous = _active_for_job(db, data.job_id)
    return _build(db, data.job_id, data.name, data.lo, data.hi, user, previous=previous)


def revise(db, drill_id, data, user):
    previous = db.get(Drill, drill_id)
    if not previous:
        raise HTTPException(404, "演练不存在")
    if previous.superseded_by:
        raise HTTPException(409, "该演练已被新版本取代，请从最新版本调整区间")
    name = data.name.strip() if data.name and data.name.strip() else previous.name
    drill = _build(db, previous.job_id, name, data.lo, data.hi, user, previous=previous)
    return drill, previous


def retry(db, drill_id):
    """中断后恢复原次演练：同一记录续跑，不新建演练、不重复记管段。"""
    drill = db.get(Drill, drill_id)
    if not drill:
        raise HTTPException(404, "演练不存在")
    if drill.superseded_by:
        raise HTTPException(409, "该演练已被新版本取代，不能重试；请使用最新版本")
    if drill.approved:
        raise HTTPException(409, "已批准的演练不能重跑；如需新区间请新建版本")
    if drill.status == "completed" and drill.complete:
        raise HTTPException(409, "该演练已整批完成，无需重试")
    drill.status = "queued"
    drill.error = ""
    drill.attempts = int(drill.attempts or 0) + 1
    return drill


def _stored_rows(drill):
    rows = (drill.result or {}).get("rows") or []
    return [row for row in rows if isinstance(row, dict) and "ordinal" in row]


def process(db, drill):
    """逐段裁决并落库；同 ordinal / 同管段只记一次，中断后从已有进展继续。"""
    if drill.status not in ("queued", "running"):
        return drill
    if drill.superseded_by:
        return drill
    snapshot = drill.snapshot or {}
    validation = snapshot.get("validation") or []
    drill.status = "running"
    db.commit()
    try:
        rows = _stored_rows(drill)
        seen_ordinals = {int(row["ordinal"]) for row in rows}
        seen_segments = {str(row["segment_id"]) for row in rows}
        for ordinal, sample in enumerate(validation):
            if ordinal in seen_ordinals or str(sample["segment_id"]) in seen_segments:
                continue
            row = drill_verdict(snapshot, ordinal, sample, drill.lo, drill.hi)
            rows.append(row)
            seen_ordinals.add(ordinal)
            seen_segments.add(row["segment_id"])
            drill.result = {
                "rows": rows,
                "interval": {"lo": drill.lo, "hi": drill.hi},
                "snapshot_digest": drill.snapshot_digest,
            }
            db.commit()
        rows = sorted(rows, key=lambda r: int(r["ordinal"]))
        summary = drill_summary(rows)
        drill.result = {
            **summary,
            "rows": rows,
            "table": drill_table(rows),
            "brief": drill_brief(rows),
            "interval": {"lo": drill.lo, "hi": drill.hi},
            "snapshot_digest": drill.snapshot_digest,
            "source_result_checksum": drill.source_result_checksum,
            "complete": True,
        }
        drill.complete = True
        drill.status = "completed"
        drill.error = ""
        drill.finished_at = time.time()
        db.commit()
    except Exception as exc:
        db.rollback()
        failed = db.get(Drill, drill.id)
        if failed and not failed.superseded_by:
            failed.status = "failed"
            failed.error = str(exc)[:990]
            db.commit()
        raise
    return drill


def approval_block_reason(drill):
    if drill is None:
        return "演练不存在"
    if drill.superseded_by:
        return "该演练已被新版本取代，旧区间不得批准"
    snapshot = drill.snapshot or {}
    expected = len(snapshot.get("validation") or [])
    rows = _stored_rows(drill)
    ordinals = [int(r["ordinal"]) for r in rows]
    segments = [str(r["segment_id"]) for r in rows]
    if expected == 0:
        return "演练缺少原验证样本，不能批准"
    if len(rows) != expected or len(set(ordinals)) != expected or len(set(segments)) != expected:
        return f"整批裁决尚未齐备（已有 {len(rows)}/{expected} 段），不能批准"
    if drill.status != "completed" or not drill.complete:
        return "整批裁决尚未标记完成，不能批准"
    if snapshot.get("snapshot_digest") != drill.snapshot_digest:
        return "演练快照与冻结样本不一致，不能批准"
    interval = (drill.result or {}).get("interval") or {}
    if float(interval.get("lo", -1)) != float(drill.lo) or float(interval.get("hi", -1)) != float(drill.hi):
        return "裁决区间与演练区间不一致，不能批准"
    return ""


def approve(db, drill, user):
    reason = approval_block_reason(drill)
    if reason:
        raise HTTPException(409, reason)
    if drill.approved and drill.approved_result:
        return drill
    rows = sorted(_stored_rows(drill), key=lambda r: int(r["ordinal"]))
    frozen = {
        "summary": drill.result.get("summary", ""),
        "metrics": drill.result.get("metrics", []),
        "findings": drill.result.get("findings", []),
        "warnings": drill.result.get("warnings", []),
        "method": drill.result.get("method", ""),
        "table": drill_brief(rows),
        "interval": {"lo": drill.lo, "hi": drill.hi},
        "snapshot_digest": drill.snapshot_digest,
        "revision": drill.revision,
        "drill_id": drill.id,
        "approved_at": time.time(),
    }
    drill.approved = True
    drill.approved_by = user.email
    drill.approved_at = frozen["approved_at"]
    drill.approved_result = frozen
    return drill


def last_approved(db):
    return db.scalar(select(Drill).where(Drill.approved == True).order_by(Drill.approved_at.desc(), Drill.id.desc()))
