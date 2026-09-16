"""코스 (DRNB + 커스텀) - API 엔드포인트 (APIRouter)."""

from fastapi import APIRouter, Depends, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import rate_limit_per_request
from app.core.security import get_current_admin, get_current_user
from app.database import get_db
from app.domain.course import attraction_service, weather_service
from app.domain.course import service as course_service
from app.domain.course.models import CourseType, Difficulty
from app.domain.course.schemas import (
    GANGWON_BOUNDARY_PATH,
    AdminCourseListResponse,
    AdminCourseResponse,
    AdminCourseUpdateRequest,
    CourseCreateRequest,
    CoursePopularityItem,
    CourseUpdateRequest,
    CustomCourseDetailResponse,
    CustomCourseListResponse,
    DrnbCourseDetailResponse,
    DrnbCourseListResponse,
    LandingStatsResponse,
    NearbyAttractionListResponse,
    SigunOptionsResponse,
    WeatherBriefingResponse,
)
from app.domain.user.models import User

router = APIRouter(prefix="/courses", tags=["courses"])

# 유저당 코스 생성: 1시간에 15번 한도
_CREATE_RATE_LIMIT_MAX_REQUESTS = 15
_CREATE_RATE_LIMIT_WINDOW_SECONDS = 3600

# 유저당 코스 수정: 1시간에 30번 한도 — 생성보다 자주 일어날 수 있어서 넉넉히.
# waypoints도 생성과 동일하게 최대 500개까지
_UPDATE_RATE_LIMIT_MAX_REQUESTS = 30
_UPDATE_RATE_LIMIT_WINDOW_SECONDS = 3600

# 유저당 이미지 업로드/삭제 한도: 10분에 20번 — 업로드와 key_prefix 공유
_IMAGE_RATE_LIMIT_MAX_REQUESTS = 20
_IMAGE_RATE_LIMIT_WINDOW_SECONDS = 600


@router.get("/admin", response_model=AdminCourseListResponse, summary="코스 목록 조회 (관리자)")
async def get_admin_courses(
    course_type: CourseType | None = Query(default=None),
    keyword: str | None = Query(default=None, min_length=1, max_length=100),
    is_active: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """is_active 무관 전체 코스를 조회 (비활성 코스도 노출).
    is_active를 넘기면 그 상태만 필터링."""
    return await course_service.list_courses_for_admin(
        session=session,
        page=page,
        size=size,
        course_type=course_type,
        keyword=keyword,
        is_active=is_active,
    )


@router.patch(
    "/admin/{course_id}", response_model=AdminCourseResponse, summary="코스 활성화/비활성화"
)
async def update_admin_course(
    course_id: int,
    body: AdminCourseUpdateRequest,
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """코스를 활성화/비활성화 (조회+토글 전용, 다른 필드 수정은 지원 X)."""
    return await course_service.set_course_active_for_admin(
        session=session, course_id=course_id, is_active=body.is_active
    )


@router.get("/gangwon-boundary")
async def get_gangwon_boundary():
    """강원도 경계 geojson 원본 그대로 반환 (프론트 커스텀 코스 폼이 경유지 지역을
    백엔드와 동일한 폴리곤으로 검증하는 데 사용). 정적 공개 데이터라 인증 불필요.
    ㅡ 도 경계는 거의 안 바뀌므로 캐시 헤더를 길게 둠"""
    return FileResponse(
        GANGWON_BOUNDARY_PATH,
        media_type="application/geo+json",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/landing-stats", response_model=LandingStatsResponse)
async def get_landing_stats(session: AsyncSession = Depends(get_db)):
    """랜딩페이지 통계 요약(총 코스 수 / 누적 완주 기록 / 총 리뷰 수) 조회. 공개, 인증 불필요."""
    return await course_service.get_landing_stats(session)


@router.get("/popular", response_model=list[CoursePopularityItem])
async def get_popular(
    course_type: CourseType | None = Query(default=None),
    limit: int = Query(default=5, ge=1, le=10),
    session: AsyncSession = Depends(get_db),
):
    """완주 횟수 기준 인기 코스 목록 조회 (랜딩페이지 "인기 코스" 섹션용).
    ㅡ course_type 생략 시 DRNB+CUSTOM 통합 랭킹. 공개 정보라 인증 불필요.
    ㅡ 관리자 대시보드 통계(get_course_stats)와 동일 로직 재사용."""
    return await course_service.get_popular_courses(session, course_type, limit)


@router.get("/{course_id}/weather-briefing", response_model=WeatherBriefingResponse)
async def get_weather_briefing(
    course_id: int, request: Request, session: AsyncSession = Depends(get_db)
):
    """코스 날씨·안전 브리핑 조회 - "코스 날씨·안전 브리핑" 버튼 클릭 시 호출.
    ㅡ DRNB/CUSTOM 공통. 공개 정보라 인증 불필요.
    ㅡ 비로그인 공개 API라 캐시 미스(=실제 외부 API 호출) 시에만 IP 단위 rate limit."""
    client_ip = request.client.host if request.client else "unknown"
    return await weather_service.get_weather_briefing(
        session=session, course_id=course_id, client_ip=client_ip
    )


@router.get("/{course_id}/nearby-attractions", response_model=NearbyAttractionListResponse)
async def get_nearby_attractions(
    course_id: int, request: Request, session: AsyncSession = Depends(get_db)
):
    """코스 시작/종료점 주변 관광지 추천 목록 조회.
    ㅡ DRNB/CUSTOM 공통, 공개 정보라 인증 불필요.
    ㅡ 날씨 브리핑과 동일하게 캐시 미스 시에만 IP 단위 rate limit이 걸린다."""
    client_ip = request.client.host if request.client else "unknown"
    return await attraction_service.get_nearby_attractions(
        session=session, course_id=course_id, client_ip=client_ip
    )


@router.get("/drnb", response_model=DrnbCourseListResponse)
async def get_drnb_courses(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    brd_div: str | None = Query(default=None),
    sigun: str | None = Query(default=None),
    difficulty: Difficulty | None = Query(default=None),
    distance_min: float | None = Query(default=None, ge=0),
    distance_max: float | None = Query(default=None, ge=0),
    session: AsyncSession = Depends(get_db),
):
    """DRNB(두루누비) 코스 목록 조회 (구간/지역/난이도/거리 필터 지원)
    ㅡ 거리 필터는 프론트에서 범위 UI 가능"""
    return await course_service.get_drnb_courses(
        session=session,
        page=page,
        size=size,
        brd_div=brd_div,
        sigun=sigun,
        difficulty=difficulty,
        distance_min=distance_min,
        distance_max=distance_max,
    )


@router.get("/drnb/sigun-options", response_model=SigunOptionsResponse)
async def get_drnb_course_sigun_options(session: AsyncSession = Depends(get_db)):
    """DRNB 코스 지역 필터 드롭다운 옵션 조회 - 실제로 코스가 존재하는 시군만 반환.
    ㅡ /drnb/{course_id}보다 먼저 선언"""
    items = await course_service.get_drnb_course_sigun_options(session=session)
    return SigunOptionsResponse(items=items)


@router.get("/drnb/{course_id}", response_model=DrnbCourseDetailResponse)
async def get_drnb_course(course_id: int, session: AsyncSession = Depends(get_db)):
    """DRNB(두루누비) 코스 상세 조회"""
    return await course_service.get_drnb_course(session=session, course_id=course_id)


@router.post(
    "/custom",
    response_model=CustomCourseDetailResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(
            rate_limit_per_request(
                "course_create", _CREATE_RATE_LIMIT_MAX_REQUESTS, _CREATE_RATE_LIMIT_WINDOW_SECONDS
            )
        )
    ],
)
async def create_course(
    body: CourseCreateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """커스텀 코스 등록 (로그인 유저 전용)"""
    return await course_service.create_course(
        session=session, user_id=current_user.user_id, body=body
    )


@router.get("/custom", response_model=CustomCourseListResponse)
async def get_custom_courses(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    created_by: int | None = Query(default=None, ge=1),
    sigun: str | None = Query(default=None),
    difficulty: Difficulty | None = Query(default=None),
    distance_min: float | None = Query(default=None, ge=0),
    distance_max: float | None = Query(default=None, ge=0),
    session: AsyncSession = Depends(get_db),
):
    """커스텀 코스 목록 조회 (전체 공개. created_by/지역/난이도/거리 필터 지원)
    ㅡ sigun은 시작 또는 종료 지역 중 하나만 일치해도 매칭
    ㅡ 거리 필터는 프론트에서 범위 UI 가능"""
    return await course_service.get_custom_courses(
        session=session,
        page=page,
        size=size,
        created_by=created_by,
        sigun=sigun,
        difficulty=difficulty,
        distance_min=distance_min,
        distance_max=distance_max,
    )


@router.get("/custom/sigun-options", response_model=SigunOptionsResponse)
async def get_custom_course_sigun_options(session: AsyncSession = Depends(get_db)):
    """커스텀 코스 지역 필터 드롭다운 옵션 조회 - 실제로 코스가 존재하는 시군만 반환.
    ㅡ /custom/{course_id}보다 먼저 선언"""
    items = await course_service.get_custom_course_sigun_options(session=session)
    return SigunOptionsResponse(items=items)


@router.get("/custom/{course_id}", response_model=CustomCourseDetailResponse)
async def get_custom_course(course_id: int, session: AsyncSession = Depends(get_db)):
    """커스텀 코스 상세 조회 (경유지/이미지 포함)"""
    return await course_service.get_custom_course(session=session, course_id=course_id)


@router.patch(
    "/custom/{course_id}",
    response_model=CustomCourseDetailResponse,
    dependencies=[
        Depends(
            rate_limit_per_request(
                "course_update", _UPDATE_RATE_LIMIT_MAX_REQUESTS, _UPDATE_RATE_LIMIT_WINDOW_SECONDS
            )
        )
    ],
)
async def update_course(
    course_id: int,
    body: CourseUpdateRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """커스텀 코스 수정 (작성자 본인 전용, 부분 수정)"""
    return await course_service.update_course(
        session=session, user_id=current_user.user_id, course_id=course_id, body=body
    )


@router.delete("/custom/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course(
    course_id: int,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """커스텀 코스 삭제 (작성자 본인 전용, Soft Delete)"""
    await course_service.delete_course(
        session=session, user_id=current_user.user_id, course_id=course_id
    )


@router.post(
    "/custom/{course_id}/images",
    response_model=CustomCourseDetailResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(
            rate_limit_per_request(
                "course_image", _IMAGE_RATE_LIMIT_MAX_REQUESTS, _IMAGE_RATE_LIMIT_WINDOW_SECONDS
            )
        )
    ],
)
async def upload_course_image(
    course_id: int,
    file: UploadFile,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """커스텀 코스 이미지 업로드 (작성자 본인 전용)"""
    return await course_service.upload_course_image(
        session=session, user_id=current_user.user_id, course_id=course_id, file=file
    )


@router.delete(
    "/custom/{course_id}/images/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[
        Depends(
            # 업로드와 key_prefix 공유 — 업로드/삭제 번갈아 반복하는 남용도 방지
            rate_limit_per_request(
                "course_image", _IMAGE_RATE_LIMIT_MAX_REQUESTS, _IMAGE_RATE_LIMIT_WINDOW_SECONDS
            )
        )
    ],
)
async def delete_course_image(
    course_id: int,
    image_id: int,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """커스텀 코스 이미지 삭제 (작성자 본인 전용)"""
    await course_service.delete_course_image(
        session=session, user_id=current_user.user_id, course_id=course_id, image_id=image_id
    )
