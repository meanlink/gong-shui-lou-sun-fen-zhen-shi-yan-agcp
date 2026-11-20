import hashlib, json
def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
def dataset_dict(d):
    return {'id':d.id, 'name':d.name, 'source':d.source, 'rows':len(d.records), 'checksum':d.checksum, 'created_at':d.created_at}
def job_dict(j):
    return {k:getattr(j,k) for k in ['id','name','dataset_id','params','status','result','error','approved','created_at','finished_at']}
def report_dict(r):
    return {k:getattr(r,k) for k in ['id','name','job_id','destination','interval_seconds','next_run','enabled']}
def delivery_dict(d):
    return {k:getattr(d,k) for k in ['id','report_id','created_at','checksum','filename','destination']}
def drill_dict(d):
    data={k:getattr(d,k) for k in ['id','name','job_id','dataset_id','lo','hi','status','error','complete','attempts','revision','previous_id','superseded_by','approved','approved_by','approved_at','snapshot_digest','source_result_checksum','created_at','finished_at','result']}
    data['segments_done']=len(((d.result or {}).get('rows') or []))
    data['segments_expected']=len(((d.snapshot or {}).get('validation') or []))
    return data
def drill_row_dict(d):
    data=drill_dict(d)
    data.pop('result',None)
    return data

