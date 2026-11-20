from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select

from src.config.database import get_db
from src.middlewares.auth import admin_only, can_write, current_user
from src.models import Drill
from src.schemas.drill import DrillInput, DrillReviseInput
from src.services import drills
from src.services.audit import record
from src.services.business import metadata
from src.utils.csv import to_csv
from src.utils.serialization import drill_dict, drill_row_dict

router = APIRouter(prefix='/drills')


def _get(db, drill_id):
    drill = db.get(Drill, drill_id)
    if not drill:
        raise HTTPException(404, '演练不存在')
    return drill


@router.get('')
def listing(db=Depends(get_db), u=Depends(current_user)):
    rows = db.scalars(select(Drill).order_by(Drill.id.desc()).limit(100))
    return [drill_row_dict(d) for d in rows]


@router.get('/schema')
def schema():
    return metadata()['drill']


@router.post('', status_code=201)
def create(data: DrillInput, db=Depends(get_db), u=Depends(can_write)):
    d = drills.create(db, data, u)
    record(db, u.email, 'drill.create', {'id': d.id, 'job': d.job_id, 'lo': d.lo, 'hi': d.hi})
    db.commit()
    return drill_dict(d)


@router.get('/{id}')
def detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    return drill_dict(_get(db, id))


@router.post('/{id}/revise', status_code=201)
def revise(id: int, data: DrillReviseInput, db=Depends(get_db), u=Depends(can_write)):
    drill, previous = drills.revise(db, id, data, u)
    record(db, u.email, 'drill.revise', {'from': previous.id, 'to': drill.id, 'lo': drill.lo, 'hi': drill.hi})
    db.commit()
    return {'drill': drill_dict(drill), 'previous': drill_dict(previous)}


@router.post('/{id}/retry')
def retry(id: int, db=Depends(get_db), u=Depends(can_write)):
    d = drills.retry(db, id)
    record(db, u.email, 'drill.retry', {'id': d.id, 'attempt': d.attempts, 'resume_from': len((d.result or {}).get('rows') or [])})
    db.commit()
    return drill_dict(d)


@router.post('/{id}/approve')
def approve(id: int, db=Depends(get_db), u=Depends(admin_only)):
    d = _get(db, id)
    drills.approve(db, d, u)
    record(db, u.email, 'drill.approve', {'id': d.id, 'revision': d.revision, 'digest': d.snapshot_digest})
    db.commit()
    return drill_dict(d)


@router.get('/{id}/brief')
def brief(id: int, db=Depends(get_db), u=Depends(current_user)):
    d = _get(db, id)
    if not d.approved or not d.approved_result:
        raise HTTPException(409, '只有已批准的演练才能生成待复测简报')
    return {'drill_id': d.id, 'revision': d.revision, 'interval': d.approved_result.get('interval'), 'approved_at': d.approved_at, 'approved_by': d.approved_by, 'table': d.approved_result.get('table') or {'columns': [], 'rows': []}}


@router.get('/{id}/brief.csv')
def brief_csv(id: int, db=Depends(get_db), u=Depends(current_user)):
    d = _get(db, id)
    if not d.approved or not d.approved_result:
        raise HTTPException(409, '只有已批准的演练才能下载待复测简报')
    table = d.approved_result.get('table') or {}
    data = to_csv(table.get('columns', []), table.get('rows', []))
    record(db, u.email, 'drill.brief.download', {'id': d.id, 'revision': d.revision})
    db.commit()
    return Response(content=data, media_type='text/csv; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="drill-{d.id}-r{d.revision}.csv"'})
