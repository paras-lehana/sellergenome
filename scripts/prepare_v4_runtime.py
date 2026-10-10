"""Create a consistent isolated preview; never overwrite an existing runtime."""
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

root=Path('/opt/sellergenome/private')
source=root/'v3'; target=root/'v4'
finalize = '--finalize-existing-backup' in sys.argv
if (target/'data/persona.sqlite3').exists() and not finalize:
    raise SystemExit('V4 runtime exists; refusing to replace it.')
(target/'data').mkdir(parents=True,exist_ok=True)
for name in ['config','approved']:
    if (source/name).exists() and not finalize:
        shutil.copytree(source/name,target/name,dirs_exist_ok=True)
# Audio manifests and artifacts are referenced by copied job rows; retain them.
for name in ['speech-demos','speech','research']:
    if (source/'data'/name).exists():
        shutil.copytree(source/'data'/name,target/'data'/name,dirs_exist_ok=True)
with sqlite3.connect(f'file:{source}/data/persona.sqlite3?mode=ro',uri=True) as before, sqlite3.connect(target/'data/persona.sqlite3') as after:
    if not finalize:
        before.backup(after,pages=10000)
        assert after.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    tables={x[0] for x in after.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ['sync_jobs','speech_jobs','insight_cohort_jobs']:
        if table not in tables: continue
        cols={x[1] for x in after.execute('PRAGMA table_info('+table+')')}
        if 'payload' not in cols:
            after.execute("UPDATE sync_jobs SET state='cancelled',lease_token=NULL,lease_until=0,error_code='preview_quarantined' WHERE state NOT IN ('completed','failed','cancelled')")
            continue
        for ident,raw in after.execute('SELECT id,payload FROM '+table).fetchall():
            value=json.loads(raw)
            if value.get('status') not in {'completed','failed','cancelled','approved'}:
                value.update(status='cancelled',error='Copied production work quarantined for V4 preview.')
                after.execute('UPDATE '+table+' SET payload=?'+(",status='cancelled'" if 'status' in cols else '')+' WHERE id=?',(json.dumps(value),ident))
    if 'sync_schedules' in tables:
        after.execute('UPDATE sync_schedules SET enabled=0')
    if 'access_sessions' in tables: after.execute('DELETE FROM access_sessions')
    # V3 delivery intents belong to its original generations, not preview indices.
    after.execute('DELETE FROM index_outbox')
    after.commit()
print('Consistent V4 backup verified; copied jobs quarantined, sessions revoked and schedules paused.')
