import time, json, logging
from pathlib import Path
from sqlalchemy import select
from redis import Redis
from src.config.database import Session
from src.config.settings import REDIS_URL
from src.models import Job, Dataset, Report, Drill
from src.services.business import analyze
from src.services import drills
from src.services.reports import deliver
from src.services.audit import record
logging.basicConfig(level=logging.INFO)
def execute(job_id):
    with Session() as db:
        j=db.get(Job,job_id)
        if not j or j.status!='queued':return
        j.status='running'; db.commit()
        try:
            d=db.get(Dataset,j.dataset_id)
            result=analyze(d.records,j.params)
            json.dumps(result,allow_nan=False)
            j.result=result;j.status='completed';j.finished_at=time.time()
            record(db,'worker','job.completed',{'id':j.id})
        except Exception as e:
            logging.exception('Analysis failed')
            j.status='failed';j.error=str(e)[:990];j.finished_at=time.time()
            record(db,'worker','job.failed',{'id':j.id,'error':j.error})
        db.commit()
def run_drill(drill_id):
    """同一演练可重复进入；已完成或已被取代的演练只读退出，不重记管段。"""
    with Session() as db:
        d=db.get(Drill,drill_id)
        if not d or d.status not in ('queued','running') or d.superseded_by:
            return
        try:
            drills.process(db,d)
            db.refresh(d)
            record(db,'worker','drill.completed',{'id':d.id,'rows':len(d.result.get('rows',[]))})
            db.commit()
        except Exception:
            logging.exception('Drill failed')
            db.rollback()
def tick():
    with Session() as db:
        queued=list(db.scalars(select(Job.id).where(Job.status=='queued').limit(3)))
        drills_queued=list(db.scalars(select(Drill.id).where(Drill.status=='queued').limit(3)))
    for job_id in queued:execute(job_id)
    for drill_id in drills_queued:run_drill(drill_id)
    with Session() as db:
        for r in db.scalars(select(Report).where(Report.enabled==True,Report.next_run<=time.time())):
            d=deliver(db,r)
            if d:record(db,'worker','report.delivered',{'id':r.id,'delivery':d.id})
        db.commit()
def main():
    r=Redis.from_url(REDIS_URL,socket_timeout=3)
    # This Compose stack has a single worker. A crash must not strand its jobs.
    # Drills keep their last committed rows, so requeueing resumes instead of duplicating.
    with Session() as db:
        for j in db.scalars(select(Job).where(Job.status=='running')):
            j.status='queued';record(db,'worker','job.recovered',{'id':j.id})
        for d in db.scalars(select(Drill).where(Drill.status=='running')):
            d.status='queued';record(db,'worker','drill.recovered',{'id':d.id,'resume_from':len((d.result or {}).get('rows',[]))})
        db.commit()
    while True:
        Path('/tmp/heartbeat').touch()
        try:
            item=r.blpop('analysis:queue',timeout=1)
            if item:execute(int(item[1]))
            tick()
        except Exception:
            logging.exception('Worker retry');time.sleep(2)
if __name__=='__main__':main()
