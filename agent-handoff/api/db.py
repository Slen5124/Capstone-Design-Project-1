import os
from sqlalchemy import create_engine

# 개인별 비밀 저장소에서 개발용 연결 값을 제공한다.
engine = create_engine(os.environ["ALO_DATABASE_URL"], pool_pre_ping=True)
