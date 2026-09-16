"""편의시설 - 비즈니스 로직."""

import asyncio
import logging
from math import asin, cos, radians, sin, sqrt

from fastapi import HTTPException, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.clients.kakao_local import search_nearby_places
from app.config import settings
from app.domain.course.models import Course
from app.domain.facility.models import CourseFacility, Facility, FacilityType
from app.domain.facility.schemas import (
    FacilityCreateRequest,
    FacilityListResponse,
    FacilityResponse,
    FacilityUpdateRequest,
)

logger = logging.getLogger(__name__)

_EARTH_RADIUS_M = 6_371_000.0
# 위도 1도의 대략적인 거리(m).
_METERS_PER_DEGREE_LAT = 111_320.0


def _haversine_distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 좌표 사이의 지표면 거리(m) - Haversine 공식(반경 계산에 필요)

    ㅡ record 도메인(완주 인증)의 동일한 공식 가져옴.
    ㅡ 담당이 달라 공용 유틸로 묶지 않고 이 도메인 안에 중복 유지.
    """
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = radians(lat2 - lat1)
    d_lambda = radians(lng2 - lng1)
    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return 2 * _EARTH_RADIUS_M * asin(sqrt(a))

# Facility 컬럼이 nullable=False라 부분 수정 시에도 null 허용 X
_REQUIRED_FIELDS = ("facility_type", "facility_name", "latitude", "longitude", "is_active")

_UNIQUE_VIOLATION_SQLSTATE = "23505"
_DUPLICATE_FACILITY_CONSTRAINT = "uq_facility_kakao_place_type"


def _is_duplicate_facility_conflict(err: IntegrityError) -> bool:
    """IntegrityError가 (kakao_place_id, facility_type) unique 위반인지 확인.

    ㅡ DB 관련 위반까지 "이미 등록된 장소" 409로 뭉개지 않도록,
      sqlstate와 constraint 이름 확인해서 이 케이스만 정확히 골라냄.
    """
    orig = err.orig
    if getattr(orig, "sqlstate", None) != _UNIQUE_VIOLATION_SQLSTATE:
        return False
    diag = getattr(orig, "diag", None)
    return getattr(diag, "constraint_name", None) == _DUPLICATE_FACILITY_CONSTRAINT


async def _validate_course_ids(session: AsyncSession, course_ids: list[int]) -> None:
    """연결하려는 course_id가 모두 존재하는지 확인."""
    if not course_ids:
        return
    result = await session.execute(
        select(Course.course_id).where(
            Course.course_id.in_(course_ids), Course.is_active.is_(True)
        )
    )
    found_ids = set(result.scalars().all())
    missing_ids = set(course_ids) - found_ids
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"존재하지 않는 코스입니다: {sorted(missing_ids)}",
        )


async def _get_facility_for_update(session: AsyncSession, facility_id: int) -> Facility:
    """편의시설을 course_facilities까지 로드하고 행 잠금을 건 상태로 조회. 없으면 404.

    ㅡ selectinload: course_facilities 연결테이블 처음부터 같이 로드해두기
    with_for_update: 동시 수정하는 상황에서, A가 끝내고 커밋할때까지 행 잠금.
    """
    query = (
        select(Facility)
        .where(Facility.facility_id == facility_id)
        .options(selectinload(Facility.course_facilities))
        .with_for_update()
    )
    result = await session.execute(query)
    facility = result.scalar_one_or_none()
    if facility is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="편의시설을 찾을 수 없습니다."
        )
    return facility


async def _find_inactive_facility(
    session: AsyncSession, kakao_place_id: str | None, facility_type: FacilityType
) -> Facility | None:
    """같은 (kakao_place_id, facility_type) 조합의 비활성 시설을 찾음 (재활성화용).

    ㅡ kakao_place_id가 None이면 unique 제약 대상 밖이라 검색하지 않음.
    ㅡ with_for_update: 동시에 같은 조합을 재등록하는 요청이 겹쳐도 하나씩 순서대로
      처리되게 락을 걸어, 두 요청이 같은 row를 동시에 재활성화하다 매핑이 유실되는 것 방지.
    """
    if kakao_place_id is None:
        return None
    result = await session.execute(
        select(Facility)
        .where(
            Facility.kakao_place_id == kakao_place_id,
            Facility.facility_type == facility_type,
            Facility.is_active.is_(False),
        )
        .options(selectinload(Facility.course_facilities))
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def create_facility(session: AsyncSession, body: FacilityCreateRequest) -> FacilityResponse:
    """편의시설을 등록.

    ㅡ 같은 (kakao_place_id, facility_type) 조합의 비활성 시설이 있으면 새로 만들지 않고
      그 시설을 재활성화 + 최신 정보로 갱신 (비활성 row가 재등록마다 계속 쌓이는 것 방지).
    """
    # course_ids 중복 제거 → 존재 검증 → 'Facility 생성 + CourseFacility 매핑' 저장
    course_ids = list(dict.fromkeys(body.course_ids))
    await _validate_course_ids(session, course_ids)

    facility = await _find_inactive_facility(session, body.kakao_place_id, body.facility_type)
    if facility is not None:
        facility.is_active = True
        facility.facility_name = body.facility_name
        facility.facility_address = body.facility_address
        facility.latitude = body.latitude
        facility.longitude = body.longitude
        facility.is_admin_edited = True
        await session.flush()
    else:
        facility = Facility(
            facility_type=body.facility_type,
            facility_name=body.facility_name,
            facility_address=body.facility_address,
            latitude=body.latitude,
            longitude=body.longitude,
            kakao_place_id=body.kakao_place_id,
            is_admin_edited=True,
        )
        session.add(facility)

    facility.course_facilities = [CourseFacility(course_id=course_id) for course_id in course_ids]

    try:
        await session.commit()
    except IntegrityError as err:
        await session.rollback()
        if not _is_duplicate_facility_conflict(err):
            raise
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 등록된 장소+시설타입 조합입니다.",
        ) from err
    await session.refresh(facility)
    return FacilityResponse.model_validate(facility)


async def get_facility(session: AsyncSession, facility_id: int) -> FacilityResponse:
    """편의시설 상세 정보를 조회(단건 조회)."""
    facility = await session.get(Facility, facility_id)
    # 비활성화(soft delete)된 시설은 목록 조회와 동일하게 노출 X
    if facility is None or not facility.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="편의시설을 찾을 수 없습니다."
        )
    return FacilityResponse.model_validate(facility)


def _bounding_box(lat: float, lng: float, radius_m: float) -> tuple[float, float, float, float]:
    """중심좌표 기준 반경을 넉넉히 감싸는 사각형.

    ㅡ 위경도 1도당 거리 근사값으로 계산하는 사전 필터용(후보 줄이는 용도)
    ㅡ 실제 반경 판정은 _haversine_distance_m로 정확히.
    """
    lat_delta = radius_m / _METERS_PER_DEGREE_LAT
    # 경도 1도 거리는 위도가 높아질수록 cos(lat)만큼 짧아짐.
    lng_delta = radius_m / (_METERS_PER_DEGREE_LAT * max(cos(radians(lat)), 0.01))
    return lat - lat_delta, lat + lat_delta, lng - lng_delta, lng + lng_delta


async def _get_nearby_facility_ids(session: AsyncSession, course: Course) -> set[int]:
    """코스 시작/종료 좌표 반경(FACILITY_RADIUS_M) 내 활성 시설 id 집합을 계산.

    ㅡ 코스 상세 조회 시점에 거리 계산으로 자동 매칭하는 방식.
      course_facility는 이 반경 밖/안 예외를 위한 수동 오버라이드(포함/제외) 용도.
    ㅡ DB에서 위경도 bounding box로 먼저 후보를 추려낸 후,
      Python에서 정확한 haversine 거리를 계산.
    """
    if course.start_lat is None or course.start_lng is None:
        return set()

    boxes = [_bounding_box(course.start_lat, course.start_lng, settings.FACILITY_RADIUS_M)]
    if course.end_lat is not None and course.end_lng is not None:
        boxes.append(_bounding_box(course.end_lat, course.end_lng, settings.FACILITY_RADIUS_M))

    box_filter = or_(
        *[
            (Facility.latitude.between(min_lat, max_lat))
            & (Facility.longitude.between(min_lng, max_lng))
            for min_lat, max_lat, min_lng, max_lng in boxes
        ]
    )
    result = await session.execute(
        select(Facility).where(Facility.is_active.is_(True), box_filter)
    )
    nearby_ids = set()
    for facility in result.scalars().all():
        dist_from_start = _haversine_distance_m(
            course.start_lat, course.start_lng, facility.latitude, facility.longitude
        )
        dist_from_end = (
            _haversine_distance_m(
                course.end_lat, course.end_lng, facility.latitude, facility.longitude
            )
            if course.end_lat is not None and course.end_lng is not None
            else dist_from_start
        )
        if min(dist_from_start, dist_from_end) <= settings.FACILITY_RADIUS_M:
            nearby_ids.add(facility.facility_id)
    return nearby_ids


async def get_facilities(
    session: AsyncSession,
    page: int,
    size: int,
    course_id: int | None = None,
) -> FacilityListResponse:
    """편의시설 목록을 조회.

    ㅡ course_id 전달 시, (해당 코스에 명시적으로 포함 연결된 시설 + 코스 좌표 반경 내
      시설) 에서 명시적으로 제외 처리된 시설을 뺀 결과를 조회 (코스 상세 지도용).
    """
    facility_id_filter = None
    if course_id is not None:
        course = await session.get(Course, course_id)
        if course is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="코스를 찾을 수 없습니다."
            )
        linked_result = await session.execute(
            select(CourseFacility.facility_id, CourseFacility.is_excluded).where(
                CourseFacility.course_id == course_id
            )
        )
        included_ids: set[int] = set()
        excluded_ids: set[int] = set()
        for facility_id, is_excluded in linked_result.all():
            (excluded_ids if is_excluded else included_ids).add(facility_id)
        nearby_ids = await _get_nearby_facility_ids(session, course)
        facility_id_filter = (included_ids | nearby_ids) - excluded_ids

    # 필터 조건은 base_query 한곳에서만 관리, count와 목록은 base_query 재사용.
    base_query = select(Facility).where(Facility.is_active.is_(True))
    if facility_id_filter is not None:
        if not facility_id_filter:
            return FacilityListResponse(items=[], total=0, page=page, size=size)
        base_query = base_query.where(Facility.facility_id.in_(facility_id_filter))

    total = (
        await session.execute(select(func.count()).select_from(base_query.subquery()))
    ).scalar_one()

    list_query = base_query.order_by(Facility.facility_id).offset((page - 1) * size).limit(size)
    facilities = (await session.execute(list_query)).scalars().all()

    return FacilityListResponse(
        items=[FacilityResponse.model_validate(f) for f in facilities],
        total=total,
        page=page,
        size=size,
    )


# 자동 검색 대상: FacilityType -> 카카오 키워드
# ㅡ 'OTHERS'는 편의점 키워드 검색해놨는데, 편의점 아닌 시설은 수동등록 가능
# ㅡ 목록에 없는 'LOCKER'은 키워드가 애매해서 수동등록 전용
_AUTO_SYNC_KEYWORDS: dict[FacilityType, str] = {
    FacilityType.RESTROOM: "화장실",
    FacilityType.PARKING: "주차장",
    FacilityType.OTHERS: "편의점",
}


async def _fetch_nearby_places(
    facility_type: FacilityType, keyword: str, lat: float, lng: float
) -> tuple[FacilityType, str, list[dict] | Exception]:
    """카카오 검색 1건. sync_nearby_facilities에서 asyncio.gather로 병렬 실행하는 용도,
    예외 던지는 대신 반환값에 담아 한 카테고리 실패가 나머지를 막지 않게."""
    try:
        documents = await search_nearby_places(lat, lng, settings.FACILITY_RADIUS_M, keyword)
        return facility_type, keyword, documents
    except Exception as e:  # noqa: BLE001 - 여러 카테고리 중 하나 실패해도 나머지 계속 진행
        return facility_type, keyword, e


def _build_facility_rows(
    facility_type: FacilityType, keyword: str, documents: list[dict]
) -> list[dict]:
    rows = []
    for doc in documents:
        try:
            rows.append(
                {
                    "facility_type": facility_type,
                    "facility_name": doc["place_name"],
                    "facility_address": doc.get("road_address_name") or doc.get("address_name"),
                    "latitude": float(doc["y"]),
                    "longitude": float(doc["x"]),
                    "kakao_place_id": doc["id"],
                }
            )
        except (KeyError, ValueError, TypeError):
            logger.warning("카카오 '%s' 검색 결과 매핑 실패, 스킵: %s", keyword, doc.get("id"))
    return rows


async def sync_nearby_facilities(
    session: AsyncSession,
    course_id: int,
    start_lat: float,
    start_lng: float,
    end_lat: float | None = None,
    end_lng: float | None = None,
) -> None:
    """카카오 키워드 검색(화장실/주차장/편의점)으로 코스 근처 편의시설 찾아 facilities에 저장.

    ㅡ 함수 호출 시점: 커스텀 코스 생성, 좌표 수정/ 두루누비 좌표 변경사항 있을시.
      호출부(create_course 등)는 이 함수가 절대 예외를 밖으로 던지지 않는다고 전제,
      어떤 예외든 여기서 끝까지 처리.
    ㅡ 시작 좌표는 항상 검색하고, 종료 좌표는 시작과의 거리가 FACILITY_RADIUS_M을
      넘을 때만 추가로 검색.
    ㅡ 화장실/주차장/편의점 검색 asyncio.gather로 동시에 실행.
      단, DB 저장(session)은 검색 결과 전부 모은뒤 한번에 upsert.
    ㅡ 주차장도 원래 계획했던 공공API가 서버 불안정해서 이 방식 사용.
      (시드파일은 그대로 두고 운영 스케줄러에는 등록 X, 수동실행은 가능)
    ㅡ 카카오 API 실패/결과없음/DB 오류는 조용히 스킵.
      (코스 생성이나 시드 막지 않음 + 외곽 지역은 결과 0건이 정상.)
    ㅡ 관리자가 이 시설을 **비활성화**(is_admin_edited=true)했으면
      이 행 전체를 갱신하지 않음. 다시 활성화하면 자동 갱신 대상에 복귀.
    ㅡ course_facility에는 연결을 안 만듦.
      (코스 위치가 바뀌어도 강제 포함되는 시설이 생길 수 있어서)
    """
    points = [(start_lat, start_lng)]
    if (
        end_lat is not None
        and end_lng is not None
        and _haversine_distance_m(start_lat, start_lng, end_lat, end_lng)
        > settings.FACILITY_RADIUS_M
    ):
        points.append((end_lat, end_lng))

    search_results = await asyncio.gather(
        *[
            _fetch_nearby_places(facility_type, keyword, lat, lng)
            for lat, lng in points
            for facility_type, keyword in _AUTO_SYNC_KEYWORDS.items()
        ]
    )

    all_rows: list[dict] = []
    for facility_type, keyword, documents in search_results:
        if isinstance(documents, Exception):
            logger.warning(
                "카카오 '%s' 검색 실패, 스킵 (course_id=%s): %s", keyword, course_id, documents
            )
            continue
        if documents:
            all_rows.extend(_build_facility_rows(facility_type, keyword, documents))

    if not all_rows:
        return

    # 시작/종료 둘 다 검색한 경우 같은 장소가 두 지점 모두에서 잡힐 수 있어
    # (kakao_place_id, facility_type) 기준으로 중복 제거
    # ㅡ 같은 배치 안에 중복 키가 들어가 upsert 자체가 실패하지 않도록
    all_rows = list(
        {(row["kakao_place_id"], row["facility_type"]): row for row in all_rows}.values()
    )

    try:
        stmt = pg_insert(Facility).values(all_rows)
        update_cols = {
            col: stmt.excluded[col]
            for col in ("facility_name", "facility_address", "latitude", "longitude")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["kakao_place_id", "facility_type"],
            set_=update_cols,
            where=Facility.is_admin_edited.is_(False),
        )
        await session.execute(stmt)
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception(
            "편의시설 자동 동기화 DB 저장 실패, 코스 생성/시드는 계속 진행 (course_id=%s)",
            course_id,
        )


async def get_facilities_for_admin(
    session: AsyncSession,
    page: int,
    size: int,
    facility_type: FacilityType | None = None,
    is_active: bool | None = None,
    keyword: str | None = None,
) -> FacilityListResponse:
    """편의시설 목록을 조회 (관리자 전용, is_active 무관 - 재활성화 대상 확인용).
    is_active를 넘기면 그 상태만, keyword를 넘기면 시설명에 포함된 것만 필터링."""
    base_query = select(Facility)
    count_query = select(func.count()).select_from(Facility)
    if facility_type is not None:
        base_query = base_query.where(Facility.facility_type == facility_type)
        count_query = count_query.where(Facility.facility_type == facility_type)
    if is_active is not None:
        base_query = base_query.where(Facility.is_active == is_active)
        count_query = count_query.where(Facility.is_active == is_active)
    if keyword:
        escaped = keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        name_filter = Facility.facility_name.ilike(f"%{escaped}%", escape="\\")
        base_query = base_query.where(name_filter)
        count_query = count_query.where(name_filter)

    total = (await session.execute(count_query)).scalar_one()
    list_query = base_query.order_by(Facility.facility_id.desc()).offset((page - 1) * size).limit(
        size
    )
    facilities = (await session.execute(list_query)).scalars().all()

    return FacilityListResponse(
        items=[FacilityResponse.model_validate(f) for f in facilities],
        total=total,
        page=page,
        size=size,
    )


async def set_course_facility_override(
    session: AsyncSession, facility_id: int, course_id: int, is_excluded: bool
) -> None:
    """특정 코스에서 이 시설을 강제 포함(is_excluded=False)/제외(True) 처리.

    ㅡ 반경 기반 자동 매칭의 예외 케이스 전용.
      이미 override가 있으면 is_excluded만 갱신, 없으면 새로 생성.
    ㅡ 강제 제외는 비활성화로 가능하지만, 강제 포함은 관리자 페이지에서 구현 X
    (코스와 편의시설 연결 여부 구현 범위가 넓어 보류)
    """
    facility = await session.get(Facility, facility_id)
    if facility is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="편의시설을 찾을 수 없습니다."
        )
    await _validate_course_ids(session, [course_id])
    stmt = (
        pg_insert(CourseFacility)
        .values(course_id=course_id, facility_id=facility_id, is_excluded=is_excluded)
        .on_conflict_do_update(
            index_elements=["course_id", "facility_id"], set_={"is_excluded": is_excluded}
        )
    )
    await session.execute(stmt)
    await session.commit()


async def clear_course_facility_override(
    session: AsyncSession, facility_id: int, course_id: int
) -> None:
    """특정 코스-시설 조합의 포함/제외 override를 지워 반경 자동 판정으로 되돌림."""
    await session.execute(
        delete(CourseFacility).where(
            CourseFacility.facility_id == facility_id, CourseFacility.course_id == course_id
        )
    )
    await session.commit()


async def update_facility(
    session: AsyncSession,
    facility_id: int,
    body: FacilityUpdateRequest,
) -> FacilityResponse:
    """편의시설을 수정."""
    facility = await _get_facility_for_update(session, facility_id)

    # exclude_unset=True: 프론트가 그 필드를 요청에 넣었는지 안 넣었는지만 체크
    update_data = body.model_dump(exclude_unset=True, exclude={"course_ids"})
    for required_field in _REQUIRED_FIELDS:
        if required_field in update_data and update_data[required_field] is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{required_field}는 null로 변경할 수 없습니다.",
            )
    for field, value in update_data.items():
        setattr(facility, field, value)
    # is_active 토글에만 락을 걸고 푼다.
    # 비활성화(is_active=False)하면 자동 재시드가 되살리지 못함.
    # 다시 활성화하면 잠금도 풀고 자동 재시드 관리 대상 복구.
    # 이름/주소/좌표 단순 수정은 잠그지 않고 갱신 상대.
    if "is_active" in update_data:
        facility.is_admin_edited = not update_data["is_active"]

    # course_ids는 exclude_unset로 처리하면 안되어서 직접 처리
    # 프론트가 course_ids 필드 아예 안 넣으면: None ㅡ 코스연결 건들지마라
    # 프론트가 "course_ids": [] 보내면 body도 [] ㅡ 이 시설 코스연결(포함) 전부 해제
    # 프론트가 "course_ids": [1, 2] 보내면 ㅡ 코스 1,2로 연결 다시 세팅
    # ㅡ is_excluded=True(반경 안 제외 override) 행은 건들지 않음 - 별도 엔드포인트
    #   (set/clear_course_facility_override)로만 관리해 서로 독립적으로 유지
    if body.course_ids is not None:
        course_ids = list(dict.fromkeys(body.course_ids))
        await _validate_course_ids(session, course_ids)
        new_ids = set(course_ids)
        # 기존에 이미 포함(is_excluded=False)돼 있던 연결 중
        # 새 목록에도 그대로 있는 것은 건드리지 않는다
        existing_included_ids = {
            cf.course_id for cf in facility.course_facilities if not cf.is_excluded
        }
        for cf in list(facility.course_facilities):
            if not cf.is_excluded and cf.course_id not in new_ids:
                # 더 이상 포함 목록에 없는 기존 연결 제거
                facility.course_facilities.remove(cf)  # delete-orphan cascade가 DELETE 처리
            elif cf.is_excluded and cf.course_id in new_ids:
                # 같은 (course_id, facility_id)는 포함/제외를 동시에 가질 수 없어(PK),
                # 새로 포함시키는 코스에 기존 제외 override가 있었다면 같이 정리
                facility.course_facilities.remove(cf)
        await session.flush()
        facility.course_facilities.extend(
            CourseFacility(course_id=course_id, is_excluded=False)
            for course_id in new_ids - existing_included_ids
        )

    try:
        await session.commit()
    except IntegrityError as err:
        await session.rollback()
        if not _is_duplicate_facility_conflict(err):
            raise
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 등록된 장소+시설타입 조합입니다.",
        ) from err
    await session.refresh(facility)
    return FacilityResponse.model_validate(facility)


async def delete_facility(session: AsyncSession, facility_id: int) -> None:
    """편의시설을 비활성화 (Soft Delete: is_active=False).

    ㅡ is_admin_edited=True로 같이 표시해, 자동 재시드가 이 시설을 재발견해도
      다시 활성화하지 않게 한다.
    """
    facility = await _get_facility_for_update(session, facility_id)
    facility.is_active = False
    facility.is_admin_edited = True
    await session.commit()
