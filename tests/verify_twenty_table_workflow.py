"""Run with the service account on the restored verification DB."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import MetaData, Table, create_engine, insert, select, text, update
from sqlalchemy.exc import IntegrityError


def run(database_url, extension_path):
    spec = importlib.util.spec_from_file_location('schedule_extensions', extension_path)
    extensions = importlib.util.module_from_spec(spec)
    sys.modules['schedule_extensions'] = extensions
    spec.loader.exec_module(extensions)
    engine = create_engine(database_url)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text('SET LOCAL ROLE alo_app'))
            tables = extensions._resolve(connection, {})
            sites = Table('sites', MetaData(), autoload_with=connection, resolve_fks=False)
            projects = Table('workflow_projects', MetaData(), autoload_with=connection, resolve_fks=False)
            project = dict(connection.execute(select(projects)).mappings().first())
            site = dict(connection.execute(select(sites).where(sites.c.code == project['site_code'])).mappings().one())
            tasks = tables['schedule_tasks']
            task = dict(connection.execute(select(tasks).where(tasks.c.schedule_id == extensions._uuid(project['schedule_id']))
                        .order_by(tasks.c.display_order)).mappings().first())
            organization_ids = [connection.execute(text('INSERT INTO organizations(name) VALUES (:name) RETURNING id'),
                                {'name': name}).scalar_one() for name in ['DB20 field fixture', 'DB20 HQ fixture']]
            actors = [connection.execute(text('INSERT INTO users(auth_subject,display_name) VALUES (:subject,:name) RETURNING id'),
                      {'subject': str(uuid4()), 'name': name}).scalar_one() for name in ['Field fixture', 'HQ fixture']]
            for actor, organization, role in zip(actors, organization_ids, ['field', 'hq']):
                connection.execute(text('INSERT INTO site_memberships(site_id,user_id,organization_id,role) VALUES (:site,:actor,:org,:role)'),
                                   {'site': site['id'], 'actor': actor, 'org': organization, 'role': role})
            week = max(1, (task['start'] - extensions._date(project['project_start'])).days // 7 + 1)
            request_id = uuid4()
            payload = {'request_id': str(request_id), 'task_id': str(task['id']), 'recipient_org_id': str(organization_ids[1]),
                       'proposed_finish': (task['finish'] + timedelta(days=7)).isoformat(), 'reason': 'Fixture extension reason',
                       'expected_start': task['start'].isoformat(), 'expected_finish': task['finish'].isoformat()}
            extensions.create_extension(connection, tables, site, project, week, actors[0], payload)
            assert extensions.create_extension(connection, tables, site, project, week, actors[0], payload)['idempotent']
            try:
                extensions.decide_extension(connection, tables, site, project, week, request_id, actors[0], {'outcome': 'accepted'})
            except HTTPException as error:
                assert error.status_code == 403
            else:
                raise AssertionError('Request sender could decide the request')
            extensions.decide_extension(connection, tables, site, project, week, request_id, actors[1], {'outcome': 'accepted'})
            extensions.apply_extension(connection, tables, site, project, week, request_id, actors[1])
            assert extensions.apply_extension(connection, tables, site, project, week, request_id, actors[1])['idempotent']
            changed = connection.execute(select(tasks).where(tasks.c.id == task['id'])).mappings().one()
            assert changed['start'] == task['start'] and changed['finish'] == task['finish']
            assert changed['effective_finish'] == task['finish'] + timedelta(days=7)
            cycle = Table('workflow_cycles', MetaData(), autoload_with=connection, resolve_fks=False)
            batches = Table('workflow_vision_batches', MetaData(), autoload_with=connection, resolve_fks=False)
            stamp = datetime.now(timezone.utc).isoformat()
            key, batch = 'db20-fixture-' + str(uuid4()), str(uuid4())
            connection.execute(insert(cycle).values(key=key, site_code=site['code'], schedule_id=project['schedule_id'],
                               week=week, batch_id=None, status='awaiting_vision', updated_at=stamp))
            connection.execute(insert(batches).values(id=batch, cycle_key=key, status='processing', results=[],
                               digest=None, created_at=stamp, updated_at=stamp))
            connection.execute(update(batches).where(batches.c.id == batch).values(status='completed', results=[{'ok': True}]))
            assert connection.execute(select(batches.c.results).where(batches.c.id == batch)).scalar_one() == [{'ok': True}]
            attachments = Table('workflow_manager_evidence', MetaData(), autoload_with=connection, resolve_fks=False)
            snapshots = Table('workflow_report_snapshots', MetaData(), autoload_with=connection, resolve_fks=False)
            deliveries = Table('workflow_vision_deliveries', MetaData(), autoload_with=connection, resolve_fks=False)
            evidence_id, snapshot_id = str(uuid4()), str(uuid4())
            connection.execute(insert(attachments).values(id=evidence_id, cycle_key=key, task_id=str(task['id']),
                               payload={'test': True}, created_at=stamp))
            connection.execute(insert(snapshots).values(id=snapshot_id, cycle_key=key, material_digest='fixture', status='draft',
                               payload={'test': True}, report_file_id=None, created_at=stamp))
            connection.execute(update(snapshots).where(snapshots.c.id == snapshot_id).values(status='completed', report_file_id='fixture-file'))
            connection.execute(insert(deliveries).values(batch_id=batch, cycle_key=key, revision=1,
                               result_file_id='fixture-file', payload_digest='fixture', skipped=[], created_at=stamp))
            assert connection.execute(select(attachments.c.payload).where(attachments.c.id == evidence_id)).scalar_one() == {'test': True}
            assert connection.execute(select(snapshots.c.report_file_id).where(snapshots.c.id == snapshot_id)).scalar_one() == 'fixture-file'
            assert connection.execute(select(deliveries.c.skipped).where(deliveries.c.batch_id == batch)).scalar_one() == []
            early_snapshot = str(uuid4())
            connection.execute(insert(snapshots).values(id=early_snapshot,
                               cycle_key=f"{site['code']}:{project['schedule_id']}:1000", material_digest='fixture',
                               status='draft', payload={'test': 'before-cycle'}, created_at=stamp))
            assert connection.execute(select(snapshots.c.payload).where(snapshots.c.id == early_snapshot)).scalar_one() == {'test': 'before-cycle'}
            with connection.begin_nested() as savepoint:
                try:
                    connection.execute(insert(batches).values(id=batch, cycle_key=key, status='completed', results=[],
                                       created_at=stamp, updated_at=stamp))
                except IntegrityError:
                    savepoint.rollback()
                else:
                    raise AssertionError('Duplicate workflow identity was accepted')
            print('PASS: canonical extension create/decide/apply/retry, recipient authorization, original dates, all seven workflow views and JSON/duplicate handling')
        finally:
            transaction.rollback()
    engine.dispose()


if __name__ == '__main__':
    run(sys.argv[1], Path(sys.argv[2]))
