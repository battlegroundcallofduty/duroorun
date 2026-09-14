"""카카오 로컬 API 연동 - 편의시설(화장실/주차장/편의점 등) 키워드 장소검색.

ㅡ 화장실 API는 2025-02부터 좌표 필드가 빠져서 못 씀 → 카카오 검색으로 대체
  (좌표 + kakao_place_id를 한 번에 얻을 수 있음).
ㅡ DMZ 같은 심한 외곽 지역은 검색 결과가 없을 수도 있음(FEATURES.md 참고).
ㅡ 주차장 공공API도 이 방식으로 전환 (2026-09-14) → API 서버 응답이 불안정해서
  주기적으로 스케줄러를 돌리기엔 신뢰가 부족하다고 판단.
"""

import httpx

from app.config import settings

_KEYWORD_SEARCH_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
_TIMEOUT = 5.0
# 카테고리(화장실/주차장/편의점)별로 검색점 1곳당 SIZE 개수까지 가져옴.
# 카카오 기본값은 15인데 생각보다 많이 잡히고 지도 프론트 확인하며 10개로 줄임.
# 정렬 기준은 정확도순 (sort=distance 의도적으로 안 씀)
_SIZE = 10


class KakaoLocalAPIError(Exception):
    """카카오 로컬 API 호출/응답 처리 실패."""


async def search_nearby_places(lat: float, lng: float, radius_m: int, keyword: str) -> list[dict]:
    """좌표 기준 반경 내 키워드 장소검색 결과를 조회 (화장실/주차장/편의점 등 공용).

    ㅡ 카테고리 1개당(한 검색점 기준) 최대 _SIZE(10)건만 가져옴. 여러 페이지를
      순회하지 않음 (2026-09-14 결정, 위 _SIZE 참고)
    """
    headers = {"Authorization": f"KakaoAK {settings.KAKAO_MAP_REST_API_KEY}"}
    params = {
        "query": keyword,
        "x": lng,
        "y": lat,
        "radius": radius_m,
        "size": _SIZE,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.get(_KEYWORD_SEARCH_URL, params=params, headers=headers)
    except httpx.TimeoutException:
        raise KakaoLocalAPIError(
            f"카카오 로컬 API 응답이 지연되고 있습니다. (query={keyword})"
        ) from None
    except httpx.RequestError:
        raise KakaoLocalAPIError(f"카카오 로컬 API에 연결할 수 없습니다. (query={keyword})") from None

    if res.status_code != 200:
        raise KakaoLocalAPIError(
            f"카카오 로컬 API가 {res.status_code}를 반환했습니다 (query={keyword}): {res.text}"
        )

    try:
        return res.json()["documents"]
    except ValueError as e:
        raise KakaoLocalAPIError(f"카카오 로컬 API 응답이 JSON 형식이 아닙니다: {e}") from None
    except KeyError as e:
        raise KakaoLocalAPIError(f"카카오 로컬 API 응답 구조가 예상과 다릅니다: {e}") from None
