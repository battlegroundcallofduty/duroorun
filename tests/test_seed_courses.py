"""seed_courses._upsert_courses - 관리자가 is_active를 직접 잠근(is_admin_managed)
두루누비 코스는 두루누비 API 재동기화로 재활성화되지 않는지 확인."""

import uuid

from sqlalchemy import delete

from app.domain.course.models import Course, CourseType
from app.domain.course.service import set_course_active_for_admin
from app.scripts.seed_courses import _upsert_courses


def _fake_api_item(dmb_id: str, course_name: str) -> dict:
    return {
        "crsIdx": dmb_id,
        "crsKorNm": course_name,
        "crsDstnc": "5.0",
        "crsLevel": "1",
        "crsTotlRqrmHour": "60",
        "crsContents": "pytest 설명",
        "sigun": "강원 춘천시",
        "brdDiv": "해파랑길",
    }


async def test_upsert_courses_keeps_admin_locked_course_inactive(db_session):
    """관리자가 admin 페이지에서 비활성화(is_admin_managed=True)한 코스는 API 응답에
    다시 나타나도 is_active가 재활성화되지 않되, 다른 필드는 계속 최신화."""
    dmb_id = f"pytest-dmb-{uuid.uuid4().hex[:12]}"
    course = Course(
        course_type=CourseType.DRNB,
        dmb_id=dmb_id,
        course_name="pytest DRNB 코스(관리자 잠금)",
        is_active=False,
        is_admin_managed=True,
    )
    db_session.add(course)
    await db_session.commit()

    try:
        await _upsert_courses(db_session, [_fake_api_item(dmb_id, "갱신된 코스명")])
        await db_session.refresh(course)
        assert course.is_active is False
        assert course.course_name == "갱신된 코스명"
    finally:
        await db_session.execute(delete(Course).where(Course.course_id == course.course_id))
        await db_session.commit()


async def test_upsert_courses_reactivates_non_locked_course(db_session):
    """관리자가 손대지 않은(is_admin_managed=False) 코스는 기존과 동일하게
    API 응답에 다시 나타나면 자동 재활성화."""
    dmb_id = f"pytest-dmb-{uuid.uuid4().hex[:12]}"
    course = Course(
        course_type=CourseType.DRNB,
        dmb_id=dmb_id,
        course_name="pytest DRNB 코스(잠금 없음)",
        is_active=False,
        is_admin_managed=False,
    )
    db_session.add(course)
    await db_session.commit()

    try:
        await _upsert_courses(db_session, [_fake_api_item(dmb_id, "갱신된 코스명")])
        await db_session.refresh(course)
        assert course.is_active is True
    finally:
        await db_session.execute(delete(Course).where(Course.course_id == course.course_id))
        await db_session.commit()


async def test_set_course_active_for_admin_releases_lock_on_reactivate(db_session):
    """관리자가 비활성화하면 is_admin_managed가 잠기지만,
    다시 활성화로 되돌리면 잠금도 같이 풀려야 함."""
    dmb_id = f"pytest-dmb-{uuid.uuid4().hex[:12]}"
    course = Course(
        course_type=CourseType.DRNB,
        dmb_id=dmb_id,
        course_name="pytest DRNB 코스(재활성화 테스트)",
        is_active=True,
        is_admin_managed=False,
    )
    db_session.add(course)
    await db_session.commit()

    try:
        # 관리자가 비활성화 -> 잠김
        await set_course_active_for_admin(db_session, course.course_id, is_active=False)
        await db_session.refresh(course)
        assert course.is_active is False
        assert course.is_admin_managed is True

        # 관리자가 다시 활성화 -> 잠금도 같이 해제돼야 함
        await set_course_active_for_admin(db_session, course.course_id, is_active=True)
        await db_session.refresh(course)
        assert course.is_active is True
        assert course.is_admin_managed is False

        # 잠금이 풀렸으니, 이후 API에서 빠졌다 다시 나타나는 시나리오에서도
        # _upsert_courses가 정상적으로 재활성화할 수 있어야 함
        course.is_active = False  # _deactivate_missing_courses가 했을 상황을 흉내
        await db_session.commit()
        await _upsert_courses(db_session, [_fake_api_item(dmb_id, "다시 나타난 코스")])
        await db_session.refresh(course)
        assert course.is_active is True
    finally:
        await db_session.execute(delete(Course).where(Course.course_id == course.course_id))
        await db_session.commit()
