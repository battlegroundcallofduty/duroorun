"""이미 저장된 코스들의 근처 편의시설(화장실/주차장/편의점)을 한 번에 채우는
일회성 백필 스크립트.

ㅡ 배포 서버용 명령어:
docker compose exec backend python -m app.scripts.backfill_facilities
ㅡ 도커 아닌 로컬용 명령어: python -m app.scripts.backfill_facilities
ㅡ 원래 sync_nearby_facilities는 코스 생성/수정 시점에만 자동으로 불림.
이 스크립트에서는 강제로 한번씩 다 호출해서 DB 활성 코스 전체 순회
ㅡ 여러 번 실행해도 안전 (upsert라 중복 없고, is_admin_edited 락도 그대로).
ㅡ 신규 배포 환경에서도 두루누비 최초 시드(seed_courses) 직후 한 번 돌려두면 좋음.
"""

import asyncio
import logging
import selectors
import sys

from sqlalchemy import select

from app.database import AsyncSessionLocal

# Course의 관계(creator 등)를 SQLAlchemy가 해석할 수 있으려면 다른 도메인 모델도
# import돼 있어야 함 (seed_courses.py와 동일한 이유)
from app.domain.course.models import Course
from app.domain.facility import models as _facility_models  # noqa: F401
from app.domain.facility.service import sync_nearby_facilities
from app.domain.user import models as _user_models  # noqa: F401

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def backfill_facilities() -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Course).where(
                Course.is_active.is_(True),
                Course.start_lat.is_not(None),
                Course.start_lng.is_not(None),
            )
        )
        courses = result.scalars().all()
        logger.info("대상 코스 %d건", len(courses))
        for i, course in enumerate(courses, start=1):
            await sync_nearby_facilities(
                session,
                course.course_id,
                course.start_lat,
                course.start_lng,
                course.end_lat,
                course.end_lng,
            )
            logger.info("(%d/%d) course_id=%s 처리 완료", i, len(courses), course.course_id)
        logger.info("백필 완료")


if __name__ == "__main__":
    if sys.platform == "win32":
        # psycopg(async)가 Windows 기본 이벤트 루프(ProactorEventLoop)를 지원하지 않음
        asyncio.run(
            backfill_facilities(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        asyncio.run(backfill_facilities())
