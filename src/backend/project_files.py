"""Site-authorized bounded uploads. No client-controlled storage paths."""
import hashlib
import io
import os
import re
import tempfile
import warnings
import zipfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from fastapi.security import HTTPBearer
from sqlalchemy import insert

from project_service import fail


def validate_file(path, filename, purpose):
    suffix = Path(filename).suffix.lower()
    image_formats = {'.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG', '.webp': 'WEBP'}
    if suffix in image_formats:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    if image.format != image_formats[suffix] or image.width * image.height > 25_000_000:
                        fail(422, '사진 형식 또는 해상도를 확인하세요. 최대 2,500만 화소입니다.')
                    image.verify()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            fail(422, '읽을 수 있는 JPG, PNG, WebP 사진을 선택하세요.')
        return 'photo'
    if purpose == 'photo':
        fail(422, '사진은 JPG, PNG, WebP 형식이어야 합니다.')
    if suffix == '.pdf':
        with path.open('rb') as stream:
            if stream.read(5) != b'%PDF-':
                fail(422, 'PDF 형식을 확인하세요.')
        return 'attachment_pdf'
    if suffix == '.txt':
        try:
            path.read_text(encoding='utf-8')
        except UnicodeError:
            fail(422, '텍스트 파일은 UTF-8 형식이어야 합니다.')
        return 'attachment_text'
    if suffix in {'.docx', '.xlsx'}:
        try:
            with zipfile.ZipFile(path) as package:
                names = package.namelist()
                expected = 'word/document.xml' if suffix == '.docx' else 'xl/workbook.xml'
                if ('[Content_Types].xml' not in names or expected not in names or
                    any('vbaproject' in n.lower() for n in names) or
                    len(names) > 10000 or sum(x.file_size for x in package.infolist()) > 100 * 1024**2):
                    fail(422, '지원하는 Word·Excel 문서 형식이 아닙니다.')
        except (zipfile.BadZipFile, OSError):
            fail(422, '문서 파일을 읽을 수 없습니다.')
        return 'attachment_office'
    fail(422, '지원 파일은 사진, PDF, DOCX, XLSX, TXT입니다.')


def create_router(s):
    router = APIRouter(prefix='/api/v1/sites/{site_code}/files', tags=['사진·회사 간 첨부파일'])

    def identity(request: Request, credentials=Depends(HTTPBearer(auto_error=False, scheme_name="ProjectBearer"))):
        return s.authenticate(request)

    @router.post('', status_code=201, summary='파일 바이트 업로드·해시 검증·DB 등록',
                 openapi_extra={'requestBody': {'required': True, 'content': {
                     'application/octet-stream': {'schema': {'type': 'string', 'format': 'binary'}}}}})
    async def upload(site_code: str, request: Request, response: Response, file_id: UUID,
                     filename: str = Query(min_length=1, max_length=160), purpose: str = 'photo',
                     checksum: str = Header(alias='X-Content-SHA256', pattern=r'^[0-9a-f]{64}$'),
                     actor=Depends(identity)):
        if (filename in {'.', '..'} or '/' in filename or '\\' in filename or ':' in filename or
                any(ord(ch) < 32 for ch in filename) or not re.fullmatch(r'[\w. ()-]+', filename)):
            fail(422, '경로 없이 안전한 파일 이름만 입력하세요.')
        if purpose not in {'photo', 'attachment', 'report'}:
            fail(422, '지원하는 업로드 목적을 선택하세요.')
        with s.engine.connect() as c:
            ctx = s.scope(c, site_code, actor, roles={'field', 'hq', 'vision', 'agent'})
            if ((ctx['role'] == 'vision' and purpose != 'photo') or
                    (ctx['role'] == 'agent' and purpose != 'report')):
                fail(403, 'Vision은 사진, 보고서 Agent는 보고서 결과만 업로드할 수 있습니다.')
        limit = 20 * 1024**2 if purpose == 'photo' else 10 * 1024**2
        directory = s.root / 'sites' / site_code / 'uploads'
        if not directory.resolve().is_relative_to(s.root):
            fail(422, '잘못된 매장 경로입니다.')
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (str(file_id) + '_' + filename)
        staging = None
        linked = False
        try:
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
                staging = Path(output.name)
                size, hasher = 0, hashlib.sha256()
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        fail(413, '사진 20MB, 문서 10MB 이하로 업로드하세요.')
                    output.write(chunk); hasher.update(chunk)
            if size == 0 or hasher.hexdigest() != checksum:
                fail(422, '파일이 비어 있거나 SHA-256 값이 다릅니다.')
            kind = validate_file(staging, filename, purpose)
            if purpose == 'report' and (kind not in {'attachment_pdf', 'attachment_office'} or
                                       Path(filename).suffix.lower() not in {'.pdf', '.docx'}):
                fail(422, '보고서 결과는 PDF 또는 DOCX 파일이어야 합니다.')
            with s.engine.begin() as c:
                ctx = s.scope(c, site_code, actor, roles={'field', 'hq', 'vision', 'agent'}, lock=True)
                if purpose == 'report' and ctx['role'] != 'agent':
                    fail(403, '보고서 Agent만 결과 파일을 업로드할 수 있습니다.')
                files = s.table('files')
                from sqlalchemy import select
                existing = c.execute(select(files).where(files.c.id == file_id)).mappings().first()
                if existing:
                    if (existing['site_id'] != ctx['site']['id'] or existing['sha256'] != checksum or
                        existing['relative_path'] != target.relative_to(s.root).as_posix() or
                        existing['uploaded_by'] != ctx['user_id'] or existing['status'] != 'ready'):
                        fail(409, '같은 파일 ID의 내용이나 소유자가 다릅니다.')
                    s.file(c, ctx, file_id)
                    response.status_code = 200
                    return s.public_file(existing, site_code)
                try:
                    os.link(staging, target)
                    linked = True
                except FileExistsError:
                    fail(409, '저장 경로가 이미 사용 중입니다. 새 파일 ID로 다시 시도하세요.')
                row = dict(id=file_id, site_id=ctx['site']['id'],
                    relative_path=target.relative_to(s.root).as_posix(),
                    kind=('report_docx' if Path(filename).suffix.lower() == '.docx' else 'report_pdf') if purpose == 'report' else kind,
                    size_bytes=size, sha256=checksum, status='ready',
                    visibility='request' if purpose == 'attachment' else 'site',
                    uploaded_by=ctx['user_id'], created_at=s.clock())
                c.execute(insert(files).values(**row))
            linked = False
            return s.public_file(row, site_code)
        finally:
            if staging:
                staging.unlink(missing_ok=True)
            if linked:
                target.unlink(missing_ok=True)

    @router.get('/{file_id}', summary='매장·요청 참여 권한을 확인하고 파일 다운로드',
                response_class=FileResponse, responses={200: {'content': {
                    'application/octet-stream': {'schema': {'type': 'string', 'format': 'binary'}}}}})
    def download(site_code: str, file_id: UUID, actor=Depends(identity)):
        with s.engine.connect() as c:
            ctx = s.scope(c, site_code, actor, roles={'field', 'hq', 'viewer', 'vision', 'agent'})
            row = s.file(c, ctx, file_id)
        path = s.verify_bytes(row)
        return FileResponse(path, filename=path.name.split('_', 1)[-1],
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    return router
