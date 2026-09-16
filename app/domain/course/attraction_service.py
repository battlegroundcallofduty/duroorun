"""코스 주변 관광지 추천 - 비즈니스 로직."""

import logging

from fastapi import HTTPException, status
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.tour import TourAPIError
from app.clients.tour import get_nearby_attractions as fetch_nearby_attractions
from app.config import settings
from app.core.rate_limit import check_rate_limit, record_rate_limit_hit
from app.domain.course.models import Course
from app.domain.course.schemas import NearbyAttractionListResponse, NearbyAttractionResponse
from app.redis import get_redis

logger = logging.getLogger(__name__)

# 카드가 무한정 늘어나지 않도록 상한
_MAX_ATTRACTIONS = 10

# 관광공사 API 일일 트래픽 쿼터(1,000회/일) 보호용 전역 카운터 키
# ㅡ IP별이 아니라 서버 전체 호출 수를 센다.
_DAILY_QUOTA_KEY = "ratelimit:nearby_attractions_daily_quota"


async def _daily_quota_exhausted(redis: Redis | None) -> bool:
    """전역 일일 쿼터를 이미 다 썼으면 True. Redis 장애 시엔 False(막지 않음)."""
    if redis is None:
        return False
    try:
        count = await redis.get(_DAILY_QUOTA_KEY)
    except RedisError:
        return False
    return count is not None and int(count) >= settings.NEARBY_ATTRACTIONS_DAILY_QUOTA_MAX_CALLS


def _map_item(item: dict) -> NearbyAttractionResponse | None:
    try:
        return NearbyAttractionResponse(
            content_id=item.get("contentid") or None,
            title=item.get("title") or "",
            address=item.get("addr1") or None,
            image_url=item.get("firstimage") or None,
            latitude=float(item["mapy"]),
            longitude=float(item["mapx"]),
            distance_m=float(item["dist"]) if item.get("dist") not in (None, "") else None,
        )
    except (KeyError, ValueError, TypeError):
        # 좌표가 없거나 형식이 이상한 항목은 조용히 건너뜀
        # ㅡ 카드 하나 이상해도 전체 목록 실패 X
        return None


async def _fetch_points(points: list[tuple[float, float]], redis: Redis | None) -> list[dict]:
    """시작/종료 좌표에 대해 관광지를 조회하고, contentid 기준으로 중복을 제거.

    호출 하나하나가 관광공사 API 일일 쿼터를 소모하므로, 매 호출 전에 전역 쿼터를
    확인해서 이미 소진했으면 남은 좌표는 API를 부르지 않고 조용히 건너뛴다.
    """
    seen_ids: set[str] = set()
    merged: list[dict] = []
    for lat, lng in points:
        if await _daily_quota_exhausted(redis):
            logger.warning("관광공사 API 일일 쿼터 소진 - 나머지 좌표 조회 스킵")
            break
        try:
            items = await fetch_nearby_attractions(
                lat,
                lng,
                radius_m=settings.NEARBY_ATTRACTIONS_RADIUS_M,
                num_of_rows=_MAX_ATTRACTIONS,
            )
        except TourAPIError:
            # 관광지 카드는 부가 기능 - 실패해도 재시도/에러 노출 없이 조용히 건너뜀
            logger.exception("관광공사 API 조회 실패: lat=%s, lng=%s", lat, lng)
            continue
        finally:
            if redis is not None:
                await record_rate_limit_hit(
                    redis, _DAILY_QUOTA_KEY, settings.NEARBY_ATTRACTIONS_DAILY_QUOTA_WINDOW_SECONDS
                )
        for item in items:
            content_id = item.get("contentid")
            if content_id is not None and content_id in seen_ids:
                continue
            if content_id is not None:
                seen_ids.add(content_id)
            merged.append(item)
    return merged


async def get_nearby_attractions(
    session: AsyncSession, course_id: int, client_ip: str
) -> NearbyAttractionListResponse:
    """코스 시작/종료점 주변 관광지를 실시간 조회.

    ㅡ 캐싱 없음 - 공모전 규정상 관광공사 API는 로컬 저장/캐싱 없이
      실시간 호출 방식으로 활용하는 걸 권고하고, 별도 승인도 안 받은 상태라 매
      요청마다 API를 새로 호출.
    ㅡ IP당 시간당 rate limit과 별개로, 서버 전체 기준 일일 쿼터(1,000회/일)도
      `_fetch_points`에서 전역으로 관리.
    """
    course = await session.get(Course, course_id)
    if course is None or not course.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="코스를 찾을 수 없습니다."
        )

    if not course.has_verification_coords:
        return NearbyAttractionListResponse(items=[])

    # Redis는 rate limit 전용(캐싱 없음) / 장애 시 rate limit 없이 진행.
    try:
        redis = await get_redis()
    except RedisError:
        logger.exception("Redis 연결 실패, rate limit 없이 진행")
        redis = None

    # 캐싱이 없어 모든 조회가 실제 관광공사 API 호출이라, IP 단위로 rate limit.
    if redis is not None:
        rate_limit_key = f"ratelimit:nearby_attractions_fetch:{client_ip}"
        await check_rate_limit(
            redis, rate_limit_key, settings.NEARBY_ATTRACTIONS_RATE_LIMIT_MAX_REQUESTS
        )
        await record_rate_limit_hit(
            redis, rate_limit_key, settings.NEARBY_ATTRACTIONS_RATE_LIMIT_WINDOW_SECONDS
        )

    points = [(course.start_lat, course.start_lng)]
    if (course.start_lat, course.start_lng) != (course.end_lat, course.end_lng):
        points.append((course.end_lat, course.end_lng))

    raw_items = await _fetch_points(points, redis)
    mapped = [attraction for item in raw_items if (attraction := _map_item(item)) is not None]
    mapped.sort(key=lambda a: a.distance_m if a.distance_m is not None else float("inf"))
    return NearbyAttractionListResponse(items=mapped[:_MAX_ATTRACTIONS])
