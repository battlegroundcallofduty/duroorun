"""코스 (DRNB + 커스텀) - 비즈니스 로직."""

import logging

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.clients.r2 import delete_file, upload_file
from app.config import settings
from app.domain.course.models import Course, CourseImage, CourseType, CourseWaypoint, Difficulty
from app.domain.course.schemas import (
    AdminCourseListResponse,
    AdminCourseResponse,
    CourseCreateRequest,
    CoursePopularityItem,
    CourseUpdateRequest,
    CourseWaypointCreate,
    CustomCourseDetailResponse,
    CustomCourseListResponse,
    CustomCourseSummary,
    DrnbCourseDetailResponse,
    DrnbCourseListResponse,
    DrnbCourseSummary,
    LandingStatsResponse,
    find_sigungu,
)
from app.domain.facility.service import sync_nearby_facilities
from app.domain.record.models import Record
from app.domain.review.models import Review
from app.domain.review.service import get_average_difficulty, get_review_summary

logger = logging.getLogger(__name__)

# Course 컬럼은 nullable=True지만 생성시 필수값이라, 수정 시 null 허용하면 X
_REQUIRED_FIELDS = ("course_name", "distance", "difficulty", "estimated_time")

_IMAGE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def _detect_image_content_type(data: bytes) -> str | None:
    """파일 시그니처(매직바이트)로 실제 이미지 형식을 판별합니다."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _validate_range(min_value: float | None, max_value: float | None, field_label: str) -> None:
    """min > max로 뒤바뀐 필터 요청을 걸러냄."""
    if min_value is not None and max_value is not None and min_value > max_value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{field_label}_min은 {field_label}_max보다 클 수 없습니다.",
        )


def _build_waypoints(waypoints: list[CourseWaypointCreate]) -> list[CourseWaypoint]:
    """요청 좌표 리스트를 sequence(순서 번호)가 매겨진 CourseWaypoint 목록으로 변환."""
    return [
        CourseWaypoint(sequence=i, latitude=w.latitude, longitude=w.longitude)
        for i, w in enumerate(waypoints)
    ]


async def _attach_custom_course_extras(session: AsyncSession, course: Course) -> None:
    """Course 모델 컬럼이 아니라 상세 응답 한정으로만 붙이는 값들 (DB엔 미저장).

    ㅡ course.creator는 호출 전에 eager load(selectinload)돼 있어야 함
    """
    course.average_difficulty = await get_average_difficulty(session, course.course_id)
    course.review_summary = await get_review_summary(session, course.course_id)
    course.creator_nickname = course.creator.nickname if course.creator else None


async def get_drnb_courses(
    session: AsyncSession,
    page: int,
    size: int,
    brd_div: str | None = None,
    sigun: str | None = None,
    difficulty: Difficulty | None = None,
    distance_min: float | None = None,
    distance_max: float | None = None,
) -> DrnbCourseListResponse:
    """DRNB 코스 목록을 조회합니다. 시드 스크립트로 저장된 DB 정보만 사용 (배치 갱신).

    ㅡ brd_div/sigun/difficulty는 정확히 일치, distance는 min/max 범위로 필터링
    ㅡ 현재 시드 스크립트가 강원 코스만 적재하므로 DB엔 강원 코스만 존재.
    추후 다른 지역까지 시드 대상이 넓어져도 이 필터로 그대로 좁혀볼 수 있음.
    """
    _validate_range(distance_min, distance_max, "distance")

    base_query = select(Course).where(
        Course.course_type == CourseType.DRNB,
        Course.is_active.is_(True),
        Course.dmb_id.is_not(None),  # 두루누비 응답엔 dmb_id 항상 존재한다는 약속 지킬수있음
    )
    if brd_div is not None:
        base_query = base_query.where(Course.brd_div == brd_div)
    if sigun is not None:
        base_query = base_query.where(Course.sigun == sigun)
    if difficulty is not None:
        base_query = base_query.where(Course.difficulty == difficulty)
    if distance_min is not None:
        base_query = base_query.where(Course.distance >= distance_min)
    if distance_max is not None:
        base_query = base_query.where(Course.distance <= distance_max)

    total = (
        await session.execute(select(func.count()).select_from(base_query.subquery()))
    ).scalar_one()

    list_query = base_query.order_by(Course.course_id).offset((page - 1) * size).limit(size)
    courses = (await session.execute(list_query)).scalars().all()

    return DrnbCourseListResponse(
        items=[DrnbCourseSummary.model_validate(c) for c in courses],
        total=total,
        page=page,
        size=size,
    )


async def get_drnb_course_sigun_options(session: AsyncSession) -> list[str]:
    """DRNB 코스 지역 필터 드롭다운에 보여줄 시군 목록.

    ㅡ 실제로 코스가 있는 시군만 반환, DB 값 기준으로 동적으로 뽑음.
    """
    result = await session.execute(
        select(Course.sigun)
        .where(
            Course.course_type == CourseType.DRNB,
            Course.is_active.is_(True),
            Course.dmb_id.is_not(None),
            Course.sigun.is_not(None),
        )
        .distinct()
    )
    return sorted(result.scalars().all())


async def get_drnb_course(session: AsyncSession, course_id: int) -> DrnbCourseDetailResponse:
    """DRNB 코스 상세를 조회합니다. 시드 스크립트로 저장된 DB 정보만 사용 (배치 갱신)."""
    result = await session.execute(
        select(Course).where(
            Course.course_id == course_id,
            Course.course_type == CourseType.DRNB,
            Course.dmb_id.is_not(None),
        )
    )
    course = result.scalar_one_or_none()
    if course is None or not course.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="코스를 찾을 수 없습니다."
        )
    # Course 모델의 실제 컬럼이 아니라 이 응답 한정으로만 붙이는 값 - DB에는 저장되지 않는다.
    course.average_difficulty = await get_average_difficulty(session, course_id)
    course.review_summary = await get_review_summary(session, course_id)
    return DrnbCourseDetailResponse.model_validate(course)


async def _get_custom_course(
    session: AsyncSession, course_id: int, *, for_update: bool = False
) -> Course:
    """CUSTOM 코스를 경유지/이미지까지 eager load해서 조회합니다. 없으면 404.

    ㅡ for_update=True면 courses 행에 락을 걸어, 동시 수정/삭제 요청 꼬이는거 방지
    """
    query = (
        select(Course)
        .where(Course.course_id == course_id, Course.course_type == CourseType.CUSTOM)
        .options(
            selectinload(Course.waypoints),
            selectinload(Course.images),
            selectinload(Course.creator),
        )
    )
    if for_update:
        # for_update 스위치로 수정/삭제할때만 True (같은 코스 건드리는 다른 요청 끼어들지 못하게)
        query = query.with_for_update()
        # with_for_update: 이 행을 읽으면서 동시에 잠그고, 내가 끝날때까지 기다리셈
    result = await session.execute(query)
    course = result.scalar_one_or_none()
    if course is None or not course.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="코스를 찾을 수 없습니다."
        )
    return course


async def create_course(
    session: AsyncSession, user_id: int, body: CourseCreateRequest
) -> CustomCourseDetailResponse:
    """커스텀 코스를 등록합니다. 시작/종료 좌표는 첫/마지막 경유지로 자동 설정.

    ㅡ sigun(시작)/end_sigun(종료)도 좌표 기준으로 같이 계산해 저장 (find_sigungu,
      외부 API 호출 없이 로컬 폴리곤으로 판별). 코스 생성 시 1회만 계산하고,
      제목/설명만 바뀌는 수정에서는 재계산하지 않음(update_course 참고).
    """
    course = Course(
        course_type=CourseType.CUSTOM,
        course_name=body.course_name,
        created_by=user_id,
        distance=body.distance,
        difficulty=body.difficulty,
        estimated_time=body.estimated_time,
        course_description=body.course_description,
        start_lat=body.waypoints[0].latitude,
        start_lng=body.waypoints[0].longitude,
        end_lat=body.waypoints[-1].latitude,
        end_lng=body.waypoints[-1].longitude,
        sigun=find_sigungu(body.waypoints[0].latitude, body.waypoints[0].longitude),
        end_sigun=find_sigungu(body.waypoints[-1].latitude, body.waypoints[-1].longitude),
    )
    course.waypoints = _build_waypoints(body.waypoints)

    session.add(course)
    await session.commit()
    # 코스 시작점 근처 화장실/주차장/편의점을 카카오 검색으로 찾아 편의시설에 저장
    # (실패해도 코스 생성은 유지)
    await sync_nearby_facilities(
        session,
        course.course_id,
        course.start_lat,
        course.start_lng,
        course.end_lat,
        course.end_lng,
    )
    course = await _get_custom_course(session, course.course_id)
    # (방금 생성된 코스라 리뷰가 없어 항상 None이지만, 나머지 3곳과 패턴을 맞춰둔다)
    await _attach_custom_course_extras(session, course)
    return CustomCourseDetailResponse.model_validate(course)


async def get_custom_course(session: AsyncSession, course_id: int) -> CustomCourseDetailResponse:
    """커스텀 코스 상세를 조회합니다 (경유지/이미지 포함)."""
    course = await _get_custom_course(session, course_id)
    await _attach_custom_course_extras(session, course)
    return CustomCourseDetailResponse.model_validate(course)


async def get_custom_courses(
    session: AsyncSession,
    page: int,
    size: int,
    created_by: int | None = None,
    sigun: str | None = None,
    difficulty: Difficulty | None = None,
    distance_min: float | None = None,
    distance_max: float | None = None,
) -> CustomCourseListResponse:
    """커스텀 코스 목록을 조회합니다. 기본은 전체 공개, created_by 지정 시 해당 작성자 코스만.

    ㅡ sigun은 시작(sigun) 또는 종료(end_sigun) 둘 중 하나만 일치해도 매칭
      (코스가 시군 경계를 걸치는 경우, 어느 쪽으로 검색해도 찾을 수 있도록)
    ㅡ difficulty는 정확히 일치, distance는 min/max 범위로 필터링
    """
    _validate_range(distance_min, distance_max, "distance")

    base_query = select(Course).where(
        Course.course_type == CourseType.CUSTOM, Course.is_active.is_(True)
    )
    if created_by is not None:
        base_query = base_query.where(Course.created_by == created_by)
    if sigun is not None:
        base_query = base_query.where((Course.sigun == sigun) | (Course.end_sigun == sigun))
    if difficulty is not None:
        base_query = base_query.where(Course.difficulty == difficulty)
    if distance_min is not None:
        base_query = base_query.where(Course.distance >= distance_min)
    if distance_max is not None:
        base_query = base_query.where(Course.distance <= distance_max)

    total = (
        await session.execute(select(func.count()).select_from(base_query.subquery()))
    ).scalar_one()

    list_query = (
        base_query.order_by(Course.created_at.desc(), Course.course_id.desc())
        .offset((page - 1) * size)
        .limit(size)
        .options(selectinload(Course.creator))
    )
    courses = (await session.execute(list_query)).scalars().all()
    for c in courses:
        # Course 모델의 실제 컬럼이 아니라 이 응답 한정으로만 붙이는 값 - DB에는 저장되지 않는다.
        c.creator_nickname = c.creator.nickname if c.creator else None

    return CustomCourseListResponse(
        items=[CustomCourseSummary.model_validate(c) for c in courses],
        total=total,
        page=page,
        size=size,
    )


async def get_custom_course_sigun_options(session: AsyncSession) -> list[str]:
    """커스텀 코스 지역 필터 드롭다운에 보여줄 시군 목록.

    ㅡ 실제로 코스가 있는(sigun 또는 end_sigun에 값이 존재하는) 시군만 반환
    ㅡ DB 값 기준으로 동적으로 뽑음.
    ㅡ UNION으로 DB가 중복 제거된 시군 목록만 반환하게
    (코스가 많아져도 결과 크기는 시군 종류 수만큼만 유지됨).
    """
    filters = (Course.course_type == CourseType.CUSTOM, Course.is_active.is_(True))
    query = (
        select(Course.sigun.label("sigun"))
        .where(*filters, Course.sigun.is_not(None))
        .union(
            select(Course.end_sigun.label("sigun")).where(*filters, Course.end_sigun.is_not(None))
        )
    )
    result = await session.execute(query)
    return sorted(row.sigun for row in result.all())


async def update_course(
    session: AsyncSession,
    user_id: int,
    course_id: int,
    body: CourseUpdateRequest,
) -> CustomCourseDetailResponse:
    """커스텀 코스를 수정합니다 (작성자 본인 전용, 부분 수정)."""
    course = await _get_custom_course(session, course_id, for_update=True)
    if course.created_by != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="본인의 코스만 수정할 수 있습니다."
        )

    # 요청 JSON에 실제 포함된 필드만 딕셔너리로 뽑음. 생략 필드는 안 건드리고 명시적 null만 반영.
    update_data = body.model_dump(exclude_unset=True, exclude={"waypoints"})
    for required_field in _REQUIRED_FIELDS:
        if required_field in update_data and update_data[required_field] is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{required_field}는 null로 변경할 수 없습니다.",
            )
    for field, value in update_data.items():
        setattr(course, field, value)

    # waypoints 필드가 요청에 아예 없으면 기존 경유지 유지, null이면 400
    coords_changed = False
    if "waypoints" in body.model_fields_set:
        if body.waypoints is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="waypoints는 null일 수 없습니다.",
            )
        course.waypoints = []
        # flush 없이 바로 새 waypoints를 대입하면
        # 같은 flush 안에서 INSERT가 DELETE보다 먼저 실행돼
        # 유니크 제약이 일시적으로 충돌할 수 있음
        # ㅡ flush로 기존 행 삭제를 먼저 확정시킨 뒤 채워넣음
        await session.flush()
        course.waypoints = _build_waypoints(body.waypoints)
        # 시작점뿐 아니라 도착점만 바뀐 경우도 재동기화 대상
        coords_changed = (
            course.start_lat,
            course.start_lng,
            course.end_lat,
            course.end_lng,
        ) != (
            body.waypoints[0].latitude,
            body.waypoints[0].longitude,
            body.waypoints[-1].latitude,
            body.waypoints[-1].longitude,
        )
        course.start_lat = body.waypoints[0].latitude
        course.start_lng = body.waypoints[0].longitude
        course.end_lat = body.waypoints[-1].latitude
        course.end_lng = body.waypoints[-1].longitude
        # 좌표가 바뀐 수정에서만 재계산 (waypoints 요청에 없으면 이 블록 자체가 안 돔)
        course.sigun = find_sigungu(body.waypoints[0].latitude, body.waypoints[0].longitude)
        course.end_sigun = find_sigungu(body.waypoints[-1].latitude, body.waypoints[-1].longitude)

    await session.commit()
    if coords_changed:
        # 코스가 새 지역으로 이동한 경우에만(시작/도착 어느 쪽이든)
        # 코스 생성과 동일하게 시작/도착점 근처 편의시설 다시 찾아둔다.
        await sync_nearby_facilities(
            session,
            course.course_id,
            course.start_lat,
            course.start_lng,
            course.end_lat,
            course.end_lng,
        )
    course = await _get_custom_course(session, course_id)
    await _attach_custom_course_extras(session, course)
    return CustomCourseDetailResponse.model_validate(course)


async def delete_course(session: AsyncSession, user_id: int, course_id: int) -> None:
    """커스텀 코스를 비활성화합니다 (Soft Delete, 작성자 본인 전용)."""
    course = await _get_custom_course(session, course_id, for_update=True)
    if course.created_by != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="본인의 코스만 삭제할 수 있습니다."
        )
    course.is_active = False
    await session.commit()


async def _count_course_images(session: AsyncSession, course_id: int) -> int:
    """코스에 현재 저장된 이미지 개수."""
    result = await session.execute(
        select(func.count()).select_from(CourseImage).where(CourseImage.course_id == course_id)
    )
    return result.scalar_one()


def _max_image_count_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            f"코스 이미지는 최대 {settings.COURSE_IMAGE_MAX_COUNT}개까지 업로드할 수 있습니다."
        ),
    )


async def upload_course_image(
    session: AsyncSession, user_id: int, course_id: int, file: UploadFile
) -> CustomCourseDetailResponse:
    """커스텀 코스 이미지 업로드 (작성자 본인 전용).

    ㅡ 파일 읽기·검증·R2 업로드는 락 없이 수행
    ㅡ 업로드 전 개수 체크: 이미 꽉 찼으면 400, 파일 안 읽음
    ㅡ 저장 직전 for_update로 락 잡고 재확인 — 동시 업로드로 최대 개수를
      넘기지 못하게 하는 진짜 방어선. 재확인에서 탈락하면 방금 올린 R2 파일 삭제.
    """
    course = await _get_custom_course(session, course_id)
    if course.created_by != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="본인의 코스에만 이미지를 업로드할 수 있습니다.",
        )

    if await _count_course_images(session, course_id) >= settings.COURSE_IMAGE_MAX_COUNT:
        raise _max_image_count_error()

    # 파일 크기 확인 (max_bytes+1까지만 읽어서, 초과 시에도 전체를 다 읽지 않음)
    max_bytes = settings.COURSE_IMAGE_MAX_SIZE_MB * 1024 * 1024
    contents = await file.read(max_bytes + 1)
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"이미지 크기는 {settings.COURSE_IMAGE_MAX_SIZE_MB}MB 이하여야 합니다.",
        )

    # 파일 형식 확인 (Content-Type 헤더는 클라이언트가 조작 가능하므로 실제 파일 시그니처로 검증)
    detected_content_type = _detect_image_content_type(contents)
    if detected_content_type is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="jpg, png, webp, gif 형식만 업로드할 수 있습니다.",
        )

    # R2 업로드 (락 없이 — 여기가 느려도 다른 요청을 막지 않음)
    ext = _IMAGE_EXTENSIONS[detected_content_type]
    try:
        image_url = await upload_file("course-images", contents, ext, detected_content_type)
    except (ClientError, BotoCoreError) as err:
        # R2 자격증명/버킷 설정 문제 등 원인 파악용 - HTTPException은 detail만 응답에
        # 노출되고 서버 로그엔 안 남아서, 별도로 원본 예외를 남겨둠
        logger.exception("코스 이미지 R2 업로드 실패: course_id=%s", course_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="이미지 업로드에 실패했습니다.",
        ) from err

    # 여기서부터 락 — 개수 최종 재확인 + 저장을 한 덩어리로 묶음
    # (락을 반영한 최신 상태를 명시적으로 가리키도록 재할당)
    # ㅡ DB에 반영되지 않았다면 R2 파일을 반드시 되돌려 지운다.
    saved = False
    try:
        course = await _get_custom_course(session, course_id, for_update=True)
        if await _count_course_images(session, course_id) >= settings.COURSE_IMAGE_MAX_COUNT:
            raise _max_image_count_error()

        # course.images 컬렉션도 append 해야 프론트에서 업로드한 이미지 잘 보임
        image = CourseImage(course_id=course_id, image_url=image_url)
        course.images.append(image)
        await session.commit()
        saved = True
    except Exception:
        if not saved:
            await session.rollback()
            try:
                await delete_file(image_url)
            # db 반영 실패했으니 방금 r2에 올린 파일도 도로 삭제
            except (ClientError, BotoCoreError):
                logger.exception(
                    "이미지 업로드 DB 반영 실패 후 R2 보상 삭제도 실패: image_url=%s", image_url
                )
        raise

    course = await _get_custom_course(session, course_id)
    await _attach_custom_course_extras(session, course)
    return CustomCourseDetailResponse.model_validate(course)


async def delete_course_image(
    session: AsyncSession, user_id: int, course_id: int, image_id: int
) -> None:
    """커스텀 코스 이미지 삭제 (작성자 본인 전용)."""
    course = await _get_custom_course(session, course_id)
    if course.created_by != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="본인의 코스 이미지만 삭제할 수 있습니다."
        )

    image = await session.get(CourseImage, image_id)
    if image is None or image.course_id != course_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="이미지를 찾을 수 없습니다."
        )

    # DB 삭제 먼저 확정 → R2 정리
    image_url = image.image_url
    await session.delete(image)
    await session.commit()

    try:
        await delete_file(image_url)
    except (ClientError, BotoCoreError):
        logger.exception("DB 삭제 후 R2 파일 삭제 실패: image_url=%s", image_url)


async def list_courses_for_admin(
    session: AsyncSession,
    page: int,
    size: int,
    course_type: CourseType | None = None,
    keyword: str | None = None,
    is_active: bool | None = None,
) -> AdminCourseListResponse:
    """관리자가 → 코스 목록을 조회 (is_active 무관, 조회+활성화토글 전용).

    일반 코스 목록 API와 달리 비활성화된 코스도 그대로 노출.
    is_active를 넘기면 그 상태만 필터링 - None이면(기본) 상태 무관 전체.
    """
    base_query = select(Course)
    count_query = select(func.count()).select_from(Course)
    if course_type is not None:
        base_query = base_query.where(Course.course_type == course_type)
        count_query = count_query.where(Course.course_type == course_type)
    if keyword:
        escaped = keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        name_filter = Course.course_name.ilike(f"%{escaped}%", escape="\\")
        base_query = base_query.where(name_filter)
        count_query = count_query.where(name_filter)
    if is_active is not None:
        base_query = base_query.where(Course.is_active == is_active)
        count_query = count_query.where(Course.is_active == is_active)

    total = (await session.execute(count_query)).scalar_one()
    list_query = base_query.order_by(Course.course_id.desc()).offset((page - 1) * size).limit(size)
    courses = (await session.execute(list_query)).scalars().all()

    return AdminCourseListResponse(
        items=[AdminCourseResponse.model_validate(c) for c in courses],
        total=total,
        page=page,
        size=size,
    )


async def set_course_active_for_admin(
    session: AsyncSession, course_id: int, is_active: bool
) -> AdminCourseResponse:
    """관리자 - 코스를 활성화/비활성화 (다른 필드는 건드리지 않음).

    ㅡ is_active=False(비활성화)로 지정할 때만 is_admin_managed=True로 같이 잠가서,
      코스 시드로 인해 재활성화 되지 않도록.
    ㅡ is_active=True(활성화)로 되돌리면 is_admin_managed도 같이 False로 풀어줌.
      (다시 코스 시드의 관리 대상이 됨)
    """
    course = await session.get(Course, course_id)
    if course is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="코스를 찾을 수 없습니다."
        )
    course.is_active = is_active
    course.is_admin_managed = not is_active
    await session.commit()
    await session.refresh(course)
    return AdminCourseResponse.model_validate(course)


async def get_landing_stats(session: AsyncSession) -> LandingStatsResponse:
    """랜딩페이지 통계 요약(총 코스 수 / 누적 완주 기록 / 총 리뷰 수) 조회."""
    total_courses = (
        await session.execute(
            select(func.count()).select_from(Course).where(Course.is_active.is_(True))
        )
    ).scalar_one()
    total_completions = (
        await session.execute(
            select(func.count()).select_from(Record).where(Record.is_completed.is_(True))
        )
    ).scalar_one()
    # 탈퇴 유저 리뷰(user_id NULL)는 get_reviews()에서 화면 노출 제외되므로, 통계도
    # 같은 기준으로 맞춘다 - 안 그러면 "누적 리뷰 수"가 실제로 볼 수 있는 리뷰 수보다 많아짐
    total_reviews = (
        await session.execute(
            select(func.count()).select_from(Review).where(Review.user_id.is_not(None))
        )
    ).scalar_one()

    return LandingStatsResponse(
        total_courses=total_courses,
        total_completions=total_completions,
        total_reviews=total_reviews,
    )


async def get_popular_courses(
    session: AsyncSession,
    course_type: CourseType | None,
    limit: int,
    *,
    include_inactive: bool = False,
) -> list[CoursePopularityItem]:
    """완주 횟수 기준 인기 코스 랭킹 (course_type=None이면 DRNB+CUSTOM 통합).

    include_inactive=False(기본값, 공개 랜딩페이지용): 비활성화(is_active=False)된
    코스는 상세 조회 시 404가 나므로 랭킹에서도 제외한다 - 랭킹엔 뜨는데 클릭하면
    404가 나는 깨진 경험을 막기 위함.
    include_inactive=True(관리자 대시보드용): FEATURES.md에 명시된 대로 "커스텀 코스
    삭제(is_active=false)는 신규 탐색/러닝 시작에서만 제외하고 관리자 대시보드
    통계에는 계속 포함"하는 기존 스펙을 지키기 위해 비활성 코스도 포함한다.
    완주 횟수가 같으면 리뷰 개수가 많은 순으로 2차 정렬해 순서를 안정적으로 고정한다.
    Review는 Record와 별도로 Course에 N:1 관계라, 그냥 join하면 조합이 곱해져
    완주 횟수 집계가 틀어지므로 상관 서브쿼리로 따로 계산한다.
    완주 횟수·리뷰 개수까지 전부 같으면 course_id로 최종 고정한다.
    """
    review_count_subquery = (
        select(func.count(Review.review_id))
        .where(Review.course_id == Course.course_id)
        .correlate(Course)
        .scalar_subquery()
    )

    query = (
        select(
            Course.course_id,
            Course.course_name,
            Course.course_type,
            Course.difficulty,
            Course.distance,
            Course.estimated_time,
            func.count(Record.record_id).label("completion_count"),
        )
        .join(Record, Record.course_id == Course.course_id)
        .where(Record.is_completed.is_(True))
    )
    if not include_inactive:
        query = query.where(Course.is_active.is_(True))
    if course_type is not None:
        query = query.where(Course.course_type == course_type)
    query = (
        query.group_by(
            Course.course_id,
            Course.course_name,
            Course.course_type,
            Course.difficulty,
            Course.distance,
            Course.estimated_time,
        )
        .order_by(
            func.count(Record.record_id).desc(),
            review_count_subquery.desc(),
            Course.course_id.asc(),
        )
        .limit(limit)
    )

    rows = (await session.execute(query)).all()
    return [
        CoursePopularityItem(
            course_id=row.course_id,
            course_name=row.course_name,
            course_type=row.course_type,
            difficulty=row.difficulty,
            distance=row.distance,
            estimated_time=row.estimated_time,
            completion_count=row.completion_count,
        )
        for row in rows
    ]
