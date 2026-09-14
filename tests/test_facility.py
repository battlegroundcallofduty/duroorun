"""편의시설 - 관리자 오버라이드 락(is_admin_edited) / 반경 매칭 포함·제외 override /
sync_nearby_facilities의 예외 격리·다중 카테고리 테스트."""

import uuid
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, select

from app.domain.course.models import Course, CourseType
from app.domain.facility.models import CourseFacility, Facility, FacilityType
from app.domain.facility.schemas import FacilityCreateRequest, FacilityUpdateRequest
from app.domain.facility.service import (
    clear_course_facility_override,
    create_facility,
    get_facilities,
    get_facilities_for_admin,
    set_course_facility_override,
    sync_nearby_facilities,
    update_facility,
)


async def _cleanup_facility(db_session, facility_id: int) -> None:
    await db_session.execute(
        delete(CourseFacility).where(CourseFacility.facility_id == facility_id)
    )
    await db_session.execute(delete(Facility).where(Facility.facility_id == facility_id))
    await db_session.commit()


async def _make_nearby_course(db_session, *, lat: float = 37.75, lng: float = 128.9) -> Course:
    course = Course(
        course_type=CourseType.CUSTOM,
        course_name=f"pytest-facility-course-{uuid.uuid4().hex[:8]}",
        distance=1.0,
        difficulty="NORMAL",
        estimated_time=10,
        start_lat=lat,
        start_lng=lng,
        end_lat=lat,
        end_lng=lng,
    )
    db_session.add(course)
    await db_session.commit()
    await db_session.refresh(course)
    return course


async def test_create_facility_marks_admin_edited(db_session):
    """관리자가 등록한 시설은 is_admin_edited=True로 저장돼 자동 재시드가 안 건드림."""
    facility = await create_facility(
        session=db_session,
        body=FacilityCreateRequest(
            facility_type=FacilityType.LOCKER,
            facility_name="pytest 보관함",
            latitude=37.75,
            longitude=128.9,
        ),
    )
    try:
        row = await db_session.get(Facility, facility.facility_id)
        assert row.is_admin_edited is True
    finally:
        await _cleanup_facility(db_session, facility.facility_id)


async def test_update_facility_name_only_does_not_lock(db_session):
    """자동 시드로 만들어진(is_admin_edited=False) 시설의 이름/주소/좌표만 고치는 건
    락을 걸지 않는다 - 다음 자동 재검색이 최신 정보로 계속 갱신하게 두기 위함."""
    facility = Facility(
        facility_type=FacilityType.PARKING,
        facility_name="pytest 자동시드 주차장",
        latitude=37.75,
        longitude=128.9,
        external_ref=f"pytest-ext-{uuid.uuid4().hex[:8]}",
        is_admin_edited=False,
    )
    db_session.add(facility)
    await db_session.commit()
    await db_session.refresh(facility)

    try:
        await update_facility(
            session=db_session,
            facility_id=facility.facility_id,
            body=FacilityUpdateRequest(facility_name="관리자가 고친 이름"),
        )
        row = await db_session.get(Facility, facility.facility_id)
        assert row.is_admin_edited is False
        assert row.facility_name == "관리자가 고친 이름"
    finally:
        await _cleanup_facility(db_session, facility.facility_id)


async def test_update_facility_deactivate_locks_reactivate_unlocks(db_session):
    """is_active를 False로 바꾸면 락이 걸리고, 다시 True로 바꾸면 락도 같이 풀린다
    (courses.set_course_active_for_admin과 동일한 대칭 구조, 2026-09-14)."""
    facility = Facility(
        facility_type=FacilityType.PARKING,
        facility_name="pytest 자동시드 주차장(토글)",
        latitude=37.75,
        longitude=128.9,
        external_ref=f"pytest-ext-{uuid.uuid4().hex[:8]}",
        is_admin_edited=False,
    )
    db_session.add(facility)
    await db_session.commit()
    await db_session.refresh(facility)

    try:
        await update_facility(
            session=db_session, facility_id=facility.facility_id, body=FacilityUpdateRequest(is_active=False)
        )
        row = await db_session.get(Facility, facility.facility_id)
        assert row.is_active is False
        assert row.is_admin_edited is True

        await update_facility(
            session=db_session, facility_id=facility.facility_id, body=FacilityUpdateRequest(is_active=True)
        )
        row = await db_session.get(Facility, facility.facility_id)
        assert row.is_active is True
        assert row.is_admin_edited is False
    finally:
        await _cleanup_facility(db_session, facility.facility_id)


def _kakao_search_only_for(keyword_wanted: str, doc: dict):
    """search_nearby_places(lat, lng, radius_m, keyword) mock용 - keyword_wanted일 때만
    doc 하나를 반환하고, 나머지 카테고리(화장실/주차장/편의점 중 나머지 둘)는 빈 결과."""

    def _side_effect(lat, lng, radius_m, keyword):
        return [doc] if keyword == keyword_wanted else []

    return _side_effect


async def test_sync_nearby_facilities_skips_admin_created_facility(db_session):
    """관리자가 직접 등록(생성)한 시설은 카카오 재검색으로 이름/좌표가 덮어써지지 않는다.
    """
    kakao_place_id = f"pytest-kakao-{uuid.uuid4().hex[:8]}"
    facility = await create_facility(
        session=db_session,
        body=FacilityCreateRequest(
            facility_type=FacilityType.RESTROOM,
            facility_name="관리자가 등록한 이름",
            latitude=37.75,
            longitude=128.9,
            kakao_place_id=kakao_place_id,
        ),
    )
    course = await _make_nearby_course(db_session)

    fake_doc = {
        "place_name": "카카오가 새로 준 이름",
        "road_address_name": None,
        "address_name": None,
        "y": "37.75",
        "x": "128.9",
        "id": kakao_place_id,
    }
    try:
        with patch(
            "app.domain.facility.service.search_nearby_places",
            new_callable=AsyncMock,
            side_effect=_kakao_search_only_for("화장실", fake_doc),
        ):
            await sync_nearby_facilities(db_session, course.course_id, 37.75, 128.9)

        row = await db_session.get(Facility, facility.facility_id)
        assert row.facility_name == "관리자가 등록한 이름"
    finally:
        await db_session.execute(delete(Course).where(Course.course_id == course.course_id))
        await _cleanup_facility(db_session, facility.facility_id)


async def test_sync_nearby_facilities_swallows_db_errors(db_session):
    """DB 오류가 나도 예외를 밖으로 던지지 않고 세션을 롤백한다
    (create_course/seed_courses가 이 함수 호출 뒤 세션을 계속 쓸 수 있어야 함).

    ㅡ session.commit을 mock해서 오류를 유도한다.
    ㅡ rollback 이후 세션이 멀쩡한지 확인할 때, 만료된 ORM 객체 속성 대신
      미리 떼어둔 course_id 값과 새 쿼리만 사용.
    """
    course = await _make_nearby_course(db_session)
    course_id = course.course_id
    fake_doc = {
        "place_name": "카카오 장소",
        "road_address_name": None,
        "address_name": None,
        "y": "37.75",
        "x": "128.9",
        "id": f"pytest-kakao-{uuid.uuid4().hex[:8]}",
    }

    def _fake_search(lat, lng, radius_m, keyword):
        return [fake_doc] if keyword == "화장실" else []

    try:
        with (
            patch(
                "app.domain.facility.service.search_nearby_places",
                new_callable=AsyncMock,
                side_effect=_fake_search,
            ),
            patch.object(
                db_session, "commit", new_callable=AsyncMock, side_effect=RuntimeError("boom")
            ),
        ):
            # 예외가 밖으로 전파되지 않아야 통과 (전파되면 여기서 바로 테스트 실패)
            await sync_nearby_facilities(db_session, course_id, 37.75, 128.9)

        # 세션이 여전히 정상적으로 쓸 수 있는 상태인지 확인 (expired 객체 속성 대신
        # 새 쿼리로 확인)
        result = await db_session.execute(
            select(Course.course_id).where(Course.course_id == course_id)
        )
        assert result.scalar_one_or_none() == course_id
    finally:
        await db_session.execute(delete(Course).where(Course.course_id == course_id))
        await db_session.commit()


async def test_sync_nearby_facilities_searches_end_point_only_when_far(db_session):
    """시작-종료 거리가 FACILITY_RADIUS_M을 넘으면 종료 좌표도 검색하고,
    넘지 않으면(겹치는 짧은/루프 코스) 시작 좌표만 검색한다."""
    course = await _make_nearby_course(db_session)
    calls: list[tuple[float, float, str]] = []

    async def _tracking_search(lat, lng, radius_m, keyword):
        calls.append((round(lat, 4), round(lng, 4), keyword))
        return []

    try:
        with patch(
            "app.domain.facility.service.search_nearby_places",
            new_callable=AsyncMock,
            side_effect=_tracking_search,
        ):
            # 시작=종료(거리 0) -> 카테고리 3개 x 지점 1개 = 3번
            await sync_nearby_facilities(db_session, course.course_id, 37.75, 128.9, 37.75, 128.9)
            assert len(calls) == 3
            assert {c[:2] for c in calls} == {(37.75, 128.9)}

            calls.clear()
            # 시작-종료가 반경(기본 1000m)을 넘게 떨어짐 -> 카테고리 3개 x 지점 2개 = 6번
            await sync_nearby_facilities(db_session, course.course_id, 37.75, 128.9, 38.5, 128.0)
            assert len(calls) == 6
            assert {c[:2] for c in calls} == {(37.75, 128.9), (38.5, 128.0)}
    finally:
        await db_session.execute(delete(Course).where(Course.course_id == course.course_id))
        await db_session.commit()


async def test_sync_nearby_facilities_saves_all_categories(db_session):
    """화장실/주차장/편의점 세 카테고리를 동시에 검색해서 각각 맞는 facility_type으로 저장."""
    course = await _make_nearby_course(db_session)
    ids = {kw: f"pytest-kakao-{kw}-{uuid.uuid4().hex[:6]}" for kw in ("화장실", "주차장", "편의점")}

    def _fake_search(lat, lng, radius_m, keyword):
        return [
            {
                "place_name": f"pytest {keyword}",
                "road_address_name": None,
                "address_name": None,
                "y": "37.75",
                "x": "128.9",
                "id": ids[keyword],
            }
        ]

    created_ids: list[int] = []
    try:
        with patch(
            "app.domain.facility.service.search_nearby_places",
            new_callable=AsyncMock,
            side_effect=_fake_search,
        ):
            await sync_nearby_facilities(db_session, course.course_id, 37.75, 128.9)

        result = await db_session.execute(
            select(Facility).where(Facility.kakao_place_id.in_(ids.values()))
        )
        saved = {f.kakao_place_id: f for f in result.scalars().all()}
        created_ids = [f.facility_id for f in saved.values()]
        assert saved[ids["화장실"]].facility_type == FacilityType.RESTROOM
        assert saved[ids["주차장"]].facility_type == FacilityType.PARKING
        assert saved[ids["편의점"]].facility_type == FacilityType.OTHERS

        # course_facility에는 연결을 만들지 않는다 - 명시적 연결을 남기면 코스가
        # 나중에 다른 위치로 이동했을 때 그 연결이 "반경 밖 강제 포함" 예외로 남아
        # 더 이상 안 맞는 시설이 계속 노출되는 문제가 있어 일부러 안 만듦 (회귀 방지)
        linked = await db_session.execute(
            select(CourseFacility).where(CourseFacility.facility_id.in_(created_ids))
        )
        assert linked.scalars().all() == []
    finally:
        await db_session.execute(delete(Course).where(Course.course_id == course.course_id))
        for fid in created_ids:
            await _cleanup_facility(db_session, fid)


async def test_sync_nearby_facilities_dedupes_place_found_at_both_points(db_session):
    """시작/종료 반경이 겹치는 경계 부근에서 같은 장소가 두 지점 검색 모두에 걸리면
    (kakao_place_id, facility_type) 기준으로 한 행만 저장 - 안 하면 같은 배치 안
    중복 키로 upsert(ON CONFLICT DO UPDATE)가 CardinalityViolation으로 실패."""
    course = await _make_nearby_course(db_session)
    shared_id = f"pytest-kakao-shared-{uuid.uuid4().hex[:8]}"

    def _fake_search(lat, lng, radius_m, keyword):
        # 화장실만 반환 + 시작/종료 어느 좌표로 검색하든 같은 kakao_place_id
        if keyword != "화장실":
            return []
        return [
            {
                "place_name": "pytest 경계에 걸친 화장실",
                "road_address_name": None,
                "address_name": None,
                "y": str(lat),
                "x": str(lng),
                "id": shared_id,
            }
        ]

    created_ids: list[int] = []
    try:
        with patch(
            "app.domain.facility.service.search_nearby_places",
            new_callable=AsyncMock,
            side_effect=_fake_search,
        ):
            # 시작-종료가 반경을 훌쩍 넘게 떨어져 있어야 두 지점 모두 검색됨
            await sync_nearby_facilities(db_session, course.course_id, 37.75, 128.9, 38.5, 128.0)

        result = await db_session.execute(
            select(Facility).where(Facility.kakao_place_id == shared_id)
        )
        saved = result.scalars().all()
        created_ids = [f.facility_id for f in saved]
        assert len(saved) == 1
    finally:
        await db_session.execute(delete(Course).where(Course.course_id == course.course_id))
        for fid in created_ids:
            await _cleanup_facility(db_session, fid)


async def test_get_facilities_for_admin_filters_by_is_active(db_session):
    """관리자 편의시설 목록 조회 시 is_active를 넘기면 해당 상태만, 안 넘기면 전체."""
    active = await create_facility(
        session=db_session,
        body=FacilityCreateRequest(
            facility_type=FacilityType.LOCKER,
            facility_name=f"pytest 활성 보관함 {uuid.uuid4().hex[:8]}",
            latitude=37.75,
            longitude=128.9,
        ),
    )
    inactive = await create_facility(
        session=db_session,
        body=FacilityCreateRequest(
            facility_type=FacilityType.LOCKER,
            facility_name=f"pytest 비활성 보관함 {uuid.uuid4().hex[:8]}",
            latitude=37.75,
            longitude=128.9,
        ),
    )
    await update_facility(
        session=db_session,
        facility_id=inactive.facility_id,
        body=FacilityUpdateRequest(is_active=False),
    )

    try:
        active_only = await get_facilities_for_admin(
            session=db_session, page=1, size=100, facility_type=FacilityType.LOCKER, is_active=True
        )
        inactive_only = await get_facilities_for_admin(
            session=db_session, page=1, size=100, facility_type=FacilityType.LOCKER, is_active=False
        )

        active_ids = {f.facility_id for f in active_only.items}
        inactive_ids = {f.facility_id for f in inactive_only.items}

        assert active.facility_id in active_ids and inactive.facility_id not in active_ids
        assert inactive.facility_id in inactive_ids and active.facility_id not in inactive_ids
    finally:
        await _cleanup_facility(db_session, active.facility_id)
        await _cleanup_facility(db_session, inactive.facility_id)


async def test_get_facilities_for_admin_filters_by_keyword(db_session):
    """관리자 편의시설 목록 조회 시 keyword를 넘기면 시설명에 포함된 것만."""
    suffix = uuid.uuid4().hex[:8]
    matching = await create_facility(
        session=db_session,
        body=FacilityCreateRequest(
            facility_type=FacilityType.LOCKER,
            facility_name=f"pytest 검색용 보관함 {suffix}",
            latitude=37.75,
            longitude=128.9,
        ),
    )
    other = await create_facility(
        session=db_session,
        body=FacilityCreateRequest(
            facility_type=FacilityType.LOCKER,
            facility_name=f"pytest 전혀다른이름 {uuid.uuid4().hex[:8]}",
            latitude=37.75,
            longitude=128.9,
        ),
    )
    try:
        result = await get_facilities_for_admin(
            session=db_session, page=1, size=100, facility_type=FacilityType.LOCKER, keyword=suffix
        )
        ids = {f.facility_id for f in result.items}
        assert matching.facility_id in ids
        assert other.facility_id not in ids
    finally:
        await _cleanup_facility(db_session, matching.facility_id)
        await _cleanup_facility(db_session, other.facility_id)


async def test_course_facility_override_excludes_facility_within_radius(db_session):
    """반경 안에 있는 시설도 override로 제외하면 코스 편의시설 목록에서 빠진다."""
    facility = await create_facility(
        session=db_session,
        body=FacilityCreateRequest(
            facility_type=FacilityType.RESTROOM,
            facility_name="pytest 반경 안 화장실",
            latitude=37.75,
            longitude=128.9,
        ),
    )
    course = await _make_nearby_course(db_session)

    try:
        before = await get_facilities(db_session, page=1, size=50, course_id=course.course_id)
        assert any(f.facility_id == facility.facility_id for f in before.items)

        await set_course_facility_override(
            db_session,
            facility_id=facility.facility_id,
            course_id=course.course_id,
            is_excluded=True,
        )
        after = await get_facilities(db_session, page=1, size=50, course_id=course.course_id)
        assert all(f.facility_id != facility.facility_id for f in after.items)

        await clear_course_facility_override(
            db_session, facility_id=facility.facility_id, course_id=course.course_id
        )
        restored = await get_facilities(db_session, page=1, size=50, course_id=course.course_id)
        assert any(f.facility_id == facility.facility_id for f in restored.items)
    finally:
        await db_session.execute(delete(Course).where(Course.course_id == course.course_id))
        await _cleanup_facility(db_session, facility.facility_id)


async def test_course_facility_override_includes_facility_outside_radius(db_session):
    """반경 밖 시설도 override로 강제 포함하면 코스 편의시설 목록에 나온다."""
    far_facility = await create_facility(
        session=db_session,
        body=FacilityCreateRequest(
            facility_type=FacilityType.LOCKER,
            facility_name="pytest 반경 밖 보관함",
            latitude=38.5,  # FACILITY_RADIUS_M(기본 1000m)를 훌쩍 넘는 거리
            longitude=128.0,
        ),
    )
    course = await _make_nearby_course(db_session)

    try:
        before = await get_facilities(db_session, page=1, size=50, course_id=course.course_id)
        assert all(f.facility_id != far_facility.facility_id for f in before.items)

        await set_course_facility_override(
            db_session,
            facility_id=far_facility.facility_id,
            course_id=course.course_id,
            is_excluded=False,
        )
        after = await get_facilities(db_session, page=1, size=50, course_id=course.course_id)
        assert any(f.facility_id == far_facility.facility_id for f in after.items)
    finally:
        # course_facility가 courses를 참조하므로 반드시 먼저 정리
        await _cleanup_facility(db_session, far_facility.facility_id)
        await db_session.execute(delete(Course).where(Course.course_id == course.course_id))
        await db_session.commit()
