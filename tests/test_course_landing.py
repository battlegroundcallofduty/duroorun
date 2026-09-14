"""코스 - 랜딩페이지용 공개 API(인기 코스 랭킹, 통계 요약) 테스트."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import delete

from app.domain.course import service as course_service
from app.domain.course.models import Course, CourseType
from app.domain.record.models import Record
from app.domain.review.models import Review
from app.domain.user.models import User


@dataclass
class LandingTestContext:
    course_ids: list[int] = field(default_factory=list)
    user_ids: list[int] = field(default_factory=list)


@pytest_asyncio.fixture
async def ctx(db_session):
    context = LandingTestContext()
    yield context

    if context.course_ids:
        await db_session.execute(delete(Record).where(Record.course_id.in_(context.course_ids)))
        await db_session.execute(delete(Review).where(Review.course_id.in_(context.course_ids)))
    if context.user_ids:
        await db_session.execute(delete(Record).where(Record.user_id.in_(context.user_ids)))
        await db_session.execute(delete(Review).where(Review.user_id.in_(context.user_ids)))
        await db_session.execute(delete(User).where(User.user_id.in_(context.user_ids)))
    if context.course_ids:
        await db_session.execute(delete(Course).where(Course.course_id.in_(context.course_ids)))
    await db_session.commit()


async def _make_course_with_completions(db_session, ctx, *, count: int, is_active: bool = True):
    course = Course(
        course_type=CourseType.CUSTOM,
        course_name=f"pytest-landing-{uuid.uuid4().hex[:8]}",
        distance=5.0,
        is_active=is_active,
    )
    db_session.add(course)
    await db_session.flush()
    ctx.course_ids.append(course.course_id)

    for _ in range(count):
        runner = User(nickname=f"pytest-landing-{uuid.uuid4().hex[:12]}")
        db_session.add(runner)
        await db_session.flush()
        ctx.user_ids.append(runner.user_id)
        db_session.add(
            Record(
                user_id=runner.user_id,
                course_id=course.course_id,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
                is_completed=True,
            )
        )
    await db_session.commit()
    return course


# 1. 비활성화(is_active=False)된 코스는 완주 기록이 많아도 인기 코스 랭킹에서 제외된다
# (회귀 테스트 - 코드리뷰에서 지적된 버그: 랭킹엔 뜨는데 클릭하면 404가 나던 문제)
async def test_get_popular_courses_excludes_inactive_course(db_session, ctx):
    active_course = await _make_course_with_completions(db_session, ctx, count=1)
    inactive_course = await _make_course_with_completions(
        db_session, ctx, count=10, is_active=False
    )

    results = await course_service.get_popular_courses(db_session, CourseType.CUSTOM, 50)
    result_course_ids = [item.course_id for item in results]

    assert active_course.course_id in result_course_ids
    assert inactive_course.course_id not in result_course_ids


# 1-1. include_inactive=True(관리자 대시보드 경로)면 비활성 코스도 포함된다
# (FEATURES.md: "커스텀 코스 삭제는... 관리자 대시보드 통계에는 계속 포함" 스펙 유지 확인)
async def test_get_popular_courses_includes_inactive_when_requested(db_session, ctx):
    inactive_course = await _make_course_with_completions(
        db_session, ctx, count=5, is_active=False
    )

    results = await course_service.get_popular_courses(
        db_session, CourseType.CUSTOM, 50, include_inactive=True
    )
    result_course_ids = [item.course_id for item in results]

    assert inactive_course.course_id in result_course_ids


# 2. course_type 필터가 정확히 적용된다
async def test_get_popular_courses_filters_by_course_type(db_session, ctx):
    custom_course = await _make_course_with_completions(db_session, ctx, count=3)

    results = await course_service.get_popular_courses(db_session, CourseType.DRNB, 50)
    result_course_ids = [item.course_id for item in results]

    assert custom_course.course_id not in result_course_ids


# 3. 탈퇴 유저의 리뷰(user_id NULL)는 랜딩페이지 누적 리뷰 수에 포함되지 않는다
# (get_reviews의 화면 노출 기준과 통계 기준을 맞춤)
async def test_get_landing_stats_excludes_withdrawn_user_reviews(db_session, ctx):
    course = await _make_course_with_completions(db_session, ctx, count=1)

    reviewer = User(nickname=f"pytest-landing-{uuid.uuid4().hex[:12]}")
    db_session.add(reviewer)
    await db_session.flush()
    ctx.user_ids.append(reviewer.user_id)
    db_session.add(
        Review(
            user_id=reviewer.user_id,
            course_id=course.course_id,
            content="탈퇴 전 남긴 리뷰",
            difficulty="NORMAL",
        )
    )
    withdrawn_review = Review(
        user_id=None,
        course_id=course.course_id,
        content="탈퇴 유저가 남긴 리뷰",
        difficulty="EASY",
    )
    db_session.add(withdrawn_review)
    await db_session.commit()

    before = await course_service.get_landing_stats(db_session)

    await db_session.delete(withdrawn_review)
    await db_session.commit()

    after = await course_service.get_landing_stats(db_session)

    # 탈퇴 유저 리뷰를 지워도 total_reviews가 안 줄어야 함 = 애초에 안 세고 있었다는 뜻
    assert before.total_reviews == after.total_reviews
