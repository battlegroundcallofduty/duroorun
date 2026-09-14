"""한국교통안전공단 주차정보 API - 편의시설(주차장) 시드용이었음.

ㅡ 이 API는 JSON 응답(format=2)의 한글 인코딩이 깨져서 내려오는 서버측 버그가 있어
  XML(format=1)로 요청하고 xml.etree로 직접 파싱.
ㅡ **운영(배포) 서버에서는 안 씀** (2026-09-14 결정)
  이 API 서버가 페이지/시간대와 무관하게 매번 불안정한 응답을 줌.
  주차장의 실제 데이터 소스는 카카오 검색 방식으로 전환됨
ㅡ 이 클라이언트는 seed_facilities_parking.py를 수동 실행 전용.
  운영 스케줄러(scheduler.py)에는 등록 X
ㅡ 이 파일 내용 검토 거의 안함. 실제로 사용되면 검토 필요.
"""

import logging
import time
from xml.etree import ElementTree

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_PRK_STTUS_URL = f"{settings.PARKING_BASE_URL}/PrkSttusInfo"
# 2026-09-13~14 실측: 정상 응답은 항상 1초 안팎, 문제되는 요청은 그쪽 게이트웨이가
# 60초 뒤에야 스스로 504를 줌(더 기다려도 소용없음, numOfRows 크기/시간대와도 무관하게
# 무작위로 약 20% 확률 발생) - 어차피 실패할 요청을 60초씩 붙잡고 있을 이유가 없어서
# 짧게 포기하고 빨리 재시도(seed_facilities_parking._fetch_page_with_retry)하는 쪽으로
_TIMEOUT = 5.0
_SUCCESS_RESULT_CODE = "0"

# 공공데이터포털 "게이트웨이" 레벨 에러(인증키/요청한도 등) 코드 - 기술문서
# "2-1 공공데이터포털 에러코드" 참고. 이 에러는 실제 제공기관(한국교통안전공단)
# 응답과 완전히 다른 XML 구조(<OpenAPI_ServiceResponse>)로 내려온다.
_GATEWAY_ERROR_MESSAGES = {
    "4": "HTTP에러",
    "12": "해당 오픈API서비스가 없거나 폐기됨",
    "20": "서비스 접근거부",
    "22": "서비스 요청제한횟수 초과",
    "30": "등록되지 않은 서비스키",
    "31": "활용기간만료",
    "32": "등록되지 않은 IP",
    "99": "기타 에러",
}


class ParkingAPIError(Exception):
    """주차정보 API 호출/응답 처리 실패."""


def _parse_item(prk: ElementTree.Element) -> dict:
    return {field.tag: (field.text or "") for field in prk}


async def get_parking_page(page_no: int, num_of_rows: int = 1000) -> tuple[list[dict], int]:
    """한 페이지 분량의 주차장 목록과 전체 건수(totalCount)를 조회."""
    params = {
        "serviceKey": settings.PARKING_API_KEY,
        "pageNo": page_no,
        "numOfRows": num_of_rows,
        "format": 1,  # JSON(2)은 한글 인코딩이 깨져서 내려오는 서버측 버그가 있어 XML 사용
    }
    started_at = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            res = await client.get(_PRK_STTUS_URL, params=params)
    except httpx.TimeoutException:
        logger.warning(
            "주차정보 API 타임아웃(page=%d): %.1f초 만에 포기", page_no, time.monotonic() - started_at
        )
        raise ParkingAPIError("주차정보 API 응답이 지연되고 있습니다.") from None
    except httpx.RequestError:
        raise ParkingAPIError("주차정보 API에 연결할 수 없습니다.") from None
    logger.info("주차정보 API 응답(page=%d): %.1f초", page_no, time.monotonic() - started_at)

    if res.status_code != 200:
        raise ParkingAPIError(f"주차정보 API가 {res.status_code}를 반환했습니다.")

    try:
        root = ElementTree.fromstring(res.text)
    except ElementTree.ParseError as e:
        raise ParkingAPIError(f"주차정보 API 응답이 XML 형식이 아닙니다: {e}") from None

    # 공공데이터포털 게이트웨이 에러는 <response><resultCode>가 아니라
    # <OpenAPI_ServiceResponse><cmmMsgHeader>... 구조로 오므로 먼저 구분해서 처리
    if root.tag == "OpenAPI_ServiceResponse":
        reason_code = root.findtext(".//returnReasonCode") or "?"
        auth_msg = root.findtext(".//returnAuthMsg") or ""
        description = _GATEWAY_ERROR_MESSAGES.get(reason_code, "알 수 없는 게이트웨이 오류")
        raise ParkingAPIError(
            f"공공데이터포털 게이트웨이 에러 {reason_code}({auth_msg}): {description}"
        )

    result_code = root.findtext("resultCode")
    if result_code != _SUCCESS_RESULT_CODE:
        result_msg = root.findtext("resultMsg") or "알 수 없는 오류"
        raise ParkingAPIError(f"주차정보 API 에러 {result_code}: {result_msg}")

    total_count = int(root.findtext("totalCount") or "0")
    container = root.find("PrkSttusInfo")
    if container is None:
        return [], total_count

    items = [_parse_item(prk) for prk in container.findall("Prk")]
    return items, total_count
