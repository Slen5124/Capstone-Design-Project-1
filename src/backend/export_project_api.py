"""Regenerate API handoff artifacts without connecting to any database."""
import json
from pathlib import Path

from sqlalchemy import create_engine

from project_app import create_app


def main():
    root = Path(__file__).resolve().parents[2]
    output = root / 'docs' / 'api'
    output.mkdir(parents=True, exist_ok=True)
    engine = create_engine('sqlite://')
    try:
        spec = create_app(engine=engine, tables={}, identities=[]).openapi()
        (output / 'PROJECT_API.openapi.json').write_text(
            json.dumps(spec, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        lines = ['# API 경로 목록', '', '생성 원본 `project_app:create_app`. 운영 DB 접속 없이 OpenAPI에서 생성한다.', '',
                 '`{site_code}`는 매장 코드, `{schedule_id}`·`{task_id}`는 DB UUID다. 화면의 원본 간트 번호와 구분한다.', '',
                 '| 메서드 | 경로 | 기능 |', '|---|---|---|']
        count = 0
        for path, methods in spec['paths'].items():
            for method, operation in methods.items():
                if method not in {'get', 'post', 'patch', 'put', 'delete'}:
                    continue
                lines.append(f'| {method.upper()} | `{path}` | {operation.get("summary", "")} |')
                if path.startswith('/api/v1/'):
                    count += 1
        lines += ['', f'인증된 업무 API **{count}개**, 별도 상태 확인 API 1개.', '',
                  '추가 입력·응답·오류 형식은 `PROJECT_API.openapi.json` 또는 실행 서버의 `/docs`를 확인한다.', '',
                  '재생성 `python src/backend/export_project_api.py`', '']
        (output / 'ENDPOINTS.md').write_text('\n'.join(lines), encoding='utf-8')
        print(f'Exported {count} authenticated operations and /health. No database connected.')
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
