"""Authenticated API entry point. No schema creation or migration at startup."""
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import MetaData, Table, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from project_api import create_router
from project_service import ProjectService
from project_changes import create_router as changes_router
from project_files import create_router as files_router
from project_reports import create_router as reports_router


def load_identities():
    raw = os.environ.get('ALO_API_IDENTITIES', '[]')
    try:
        rows = json.loads(raw)
        if not isinstance(rows, list):
            raise ValueError()
        hashes = set()
        for row in rows:
            value = row['token_sha256']
            if not re.fullmatch('[0-9a-f]{64}', value) or value in hashes:
                raise ValueError()
            hashes.add(value)
            if row.get('kind', 'user') in {'vision', 'agent'}:
                if not row['site_ids']:
                    raise ValueError()
                for site in row['site_ids']:
                    UUID(site)
            elif row.get('kind', 'user') == 'user':
                UUID(row['user_id']); UUID(row['organization_id'])
            else:
                raise ValueError()
        return rows
    except (ValueError, TypeError, KeyError):
        # Do not print a malformed configuration that may contain credentials.
        from project_service import fail
        fail(503, 'API 인증 설정 형식을 확인해 주세요.')


def create_app(*, engine=None, tables=None, identities=None, storage_root=None, clock=None):
    if engine is None:
        from db import engine
    loaded = {} if tables is None else dict(tables)

    @asynccontextmanager
    async def lifespan(app):
        if tables is None:
            metadata = MetaData()
            with engine.connect() as c:
                for name in ('sites', 'organizations', 'users', 'site_memberships', 'schedules',
                             'schedule_tasks', 'files', 'task_events', 'task_event_files',
                             'task_observations', 'daily_records', 'schedule_task_locations', 'change_requests', 'agent_reports'):
                    loaded[name] = Table(name, metadata, autoload_with=c, resolve_fks=False)
        yield

    app = FastAPI(title='ALOHA Project API', version='1.0.0', lifespan=lifespan,
                  description='20개 canonical 테이블 · 모바일 현장 제출 · 본사 검토 · Agent 연결',
                  swagger_ui_parameters={'persistAuthorization': False})
    origins = [x.strip() for x in os.getenv('ALO_API_ALLOWED_ORIGINS', '').split(',') if x.strip()]
    if '*' in origins:
        raise ValueError('Explicit frontend origins are required')
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins,
            allow_methods=['GET', 'POST', 'PATCH', 'OPTIONS'],
            allow_headers=['Authorization', 'Content-Type', 'X-Content-SHA256'],
            expose_headers=['Content-Disposition'], allow_credentials=False)
    service = ProjectService(engine, lambda: loaded, identities if identities is not None else load_identities,
                             storage_root or os.getenv('ALO_STORAGE_ROOT', '/home/ubuntu/alo-data'), clock)
    app.state.project_service = service
    app.include_router(create_router(service))
    app.include_router(changes_router(service))
    app.include_router(files_router(service))
    app.include_router(reports_router(service))

    @app.get('/health', tags=['상태'])
    def health():
        with engine.connect() as c:
            c.execute(text('SELECT 1'))
        return {'status': 'ok'}

    @app.exception_handler(IntegrityError)
    async def conflict(request, error):
        return JSONResponse(status_code=409, content={'detail': '연결 범위 또는 중복 상태가 충돌했습니다.'})

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, error):
        logging.error('Project database request failed (%s)', type(error).__name__)
        return JSONResponse(status_code=503, content={'detail': 'DB 요청을 처리하지 못했습니다.'})

    @app.middleware('http')
    async def private_responses(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    return app
