"""코스 주변 관광지 추천 - 기본 조회 / 부분 실패 스킵 / rate limit / 일일 쿼터 테스트.

캐싱 없이 매 요청마다 관광공사 API를 실시간 호출하는 구조라(공모전 규정),
IP별 rate limit과 서버 전체 일일 쿼터(팀 확인: 1,000회/일) 가드가 제대로 도는지 확인.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import app.redis as app_redis
from app.clients.tour import TourAPIError
from app.config import settings
from app.domain.course.attraction_service import _DAILY_QUOTA_KEY, get_nearby_attractions


def _fake_item(content_id: str, dist: str) -> dict:
    return {
        "contentid": content_id,
        "title": f"pytest 관광지 {content_id}",
        "addr1": "강원도 어딘가",
        "firstimage": None,
        "mapx": "128.9",
        "mapy": "37.5",
        "dist": dist,
    }


async def _cleanup(redis_client):
    await redis_client.delete("ratelimit:nearby_attractions_fetch:127.0.0.1")
    await redis_client.delete(_DAILY_QUOTA_KEY)
    app_redis._redis = None


async def test_get_nearby_attractions_returns_items_sorted_by_distance(
    db_session, redis_client, review_test_course
):
    """시작점/종료점 두 좌표에서 각각 받은 결과를 합쳐도, 어느 좌표에서 나왔는지와
    무관하게 거리(distance_m) 기준으로 재정렬해서 반환."""
    app_redis._redis = None
    course_id = review_test_course.course_id

    try:
        with patch(
            "app.domain.course.attraction_service.fetch_nearby_attractions",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.side_effect = [
                [_fake_item("1", "500"), _fake_item("2", "100")],
                [_fake_item("3", "300")],
            ]
            response = await get_nearby_attractions(
                session=db_session, course_id=course_id, client_ip="127.0.0.1"
            )

        assert [item.content_id for item in response.items] == ["2", "3", "1"]
    finally:
        await _cleanup(redis_client)


async def test_get_nearby_attractions_skips_failed_point_silently(
    db_session, redis_client, review_test_course
):
    """시작점 조회가 실패해도 에러를 노출하지 않고, 성공한 종료점 결과만 돌려준다."""
    app_redis._redis = None
    course_id = review_test_course.course_id

    try:
        with patch(
            "app.domain.course.attraction_service.fetch_nearby_attractions",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.side_effect = [
                TourAPIError("일시 장애 시뮬레이션"),
                [_fake_item("1", "100")],
            ]
            response = await get_nearby_attractions(
                session=db_session, course_id=course_id, client_ip="127.0.0.1"
            )

        assert [item.content_id for item in response.items] == ["1"]
    finally:
        await _cleanup(redis_client)


async def test_get_nearby_attractions_rate_limited_per_ip(
    db_session, redis_client, review_test_course
):
    """IP당 시간당 한도를 이미 채웠으면 API를 부르지 않고 429."""
    app_redis._redis = None
    course_id = review_test_course.course_id
    rate_limit_key = "ratelimit:nearby_attractions_fetch:127.0.0.1"
    await redis_client.set(rate_limit_key, settings.NEARBY_ATTRACTIONS_RATE_LIMIT_MAX_REQUESTS)

    try:
        with patch(
            "app.domain.course.attraction_service.fetch_nearby_attractions",
            new_callable=AsyncMock,
        ) as mock_fetch:
            with pytest.raises(HTTPException) as exc_info:
                await get_nearby_attractions(
                    session=db_session, course_id=course_id, client_ip="127.0.0.1"
                )
            mock_fetch.assert_not_called()

        assert exc_info.value.status_code == 429
    finally:
        await _cleanup(redis_client)


async def test_get_nearby_attractions_stops_calling_api_after_daily_quota_exhausted(
    db_session, redis_client, review_test_course
):
    """서버 전체 일일 쿼터를 이미 다 썼으면 남은 좌표는 실제 API를 호출하지 않고
    조용히 건너뛴다 - IP별 제한만으로는 서로 다른 IP가 나눠서 하루 쿼터를 소진하는 걸
    막지 못해서 추가된 전역 가드."""
    app_redis._redis = None
    course_id = review_test_course.course_id
    await redis_client.set(_DAILY_QUOTA_KEY, settings.NEARBY_ATTRACTIONS_DAILY_QUOTA_MAX_CALLS)

    try:
        with patch(
            "app.domain.course.attraction_service.fetch_nearby_attractions",
            new_callable=AsyncMock,
        ) as mock_fetch:
            response = await get_nearby_attractions(
                session=db_session, course_id=course_id, client_ip="127.0.0.1"
            )
            mock_fetch.assert_not_called()

        assert response.items == []
    finally:
        await _cleanup(redis_client)


async def test_get_nearby_attractions_increments_daily_quota_counter(
    db_session, redis_client, review_test_course
):
    """실제로 호출한 만큼(시작/종료 좌표 2번) 전역 일일 쿼터 카운터가 늘어난다."""
    app_redis._redis = None
    course_id = review_test_course.course_id

    try:
        with patch(
            "app.domain.course.attraction_service.fetch_nearby_attractions",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = []
            # review_test_course는 시작(1.0,1.0)/종료(2.0,2.0) 좌표가 달라 2번 호출됨
            await get_nearby_attractions(
                session=db_session, course_id=course_id, client_ip="127.0.0.1"
            )

        assert int(await redis_client.get(_DAILY_QUOTA_KEY)) == 2
    finally:
        await _cleanup(redis_client)
