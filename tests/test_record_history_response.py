"""기록 목록(get_records) 응답에 코스명·코스 타입이 정확히 포함되는지 테스트."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete

from app.database import AsyncSessionLocal
from app.domain.course.models import Course, CourseType
from app.domain.record.models import Record
from app.domain.record.service import get_records
from app.domain.user.models import User


async def test_get_records_includes_course_name_and_type(db_session):
    course = Course(
        course_type=CourseType.CUSTOM,
        course_name=f"pytest-history-course-{uuid.uuid4().hex[:8]}",
        distance=5.0,
        difficulty="NORMAL",
        estimated_time=60,
        start_lat=1.0,
        start_lng=1.0,
        end_lat=2.0,
        end_lng=2.0,
    )
    db_session.add(course)
    user = User(nickname=f"pytest-user-{uuid.uuid4().hex[:12]}")
    db_session.add(user)
    await db_session.flush()

    record = Record(
        user_id=user.user_id,
        course_id=course.course_id,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        user_start_lat=1.0,
        user_start_lng=1.0,
        user_end_lat=2.0,
        user_end_lng=2.0,
        duration_seconds=600,
        is_completed=True,
    )
    db_session.add(record)
    await db_session.commit()

    try:
        result = await get_records(session=db_session, user_id=user.user_id, page=1, size=20)
        assert result.total == 1
        item = result.items[0]
        assert item.course_name == course.course_name
        assert item.course_type == CourseType.CUSTOM
        assert item.course_is_active is True
    finally:
        async with AsyncSessionLocal() as cleanup_session:
            await cleanup_session.execute(delete(Record).where(Record.user_id == user.user_id))
            await cleanup_session.execute(delete(User).where(User.user_id == user.user_id))
            await cleanup_session.execute(
                delete(Course).where(Course.course_id == course.course_id)
            )
            await cleanup_session.commit()


async def test_get_records_reflects_deactivated_course(db_session):
    """완주 기록의 코스가 이후 비활성화되면 course_is_active=False로 내려가야 한다 -
    프론트가 이 값으로 "리뷰 보기·작성" 링크를 막힌 코스로 안내하지 않게 판단한다(리뷰 지적).
    """
    course = Course(
        course_type=CourseType.CUSTOM,
        course_name=f"pytest-history-inactive-course-{uuid.uuid4().hex[:8]}",
        distance=5.0,
        difficulty="NORMAL",
        estimated_time=60,
        start_lat=1.0,
        start_lng=1.0,
        end_lat=2.0,
        end_lng=2.0,
        is_active=False,
    )
    db_session.add(course)
    user = User(nickname=f"pytest-user-{uuid.uuid4().hex[:12]}")
    db_session.add(user)
    await db_session.flush()

    record = Record(
        user_id=user.user_id,
        course_id=course.course_id,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        duration_seconds=600,
        is_completed=True,
    )
    db_session.add(record)
    await db_session.commit()

    try:
        result = await get_records(session=db_session, user_id=user.user_id, page=1, size=20)
        assert result.items[0].course_is_active is False
    finally:
        async with AsyncSessionLocal() as cleanup_session:
            await cleanup_session.execute(delete(Record).where(Record.user_id == user.user_id))
            await cleanup_session.execute(delete(User).where(User.user_id == user.user_id))
            await cleanup_session.execute(
                delete(Course).where(Course.course_id == course.course_id)
            )
            await cleanup_session.commit()
