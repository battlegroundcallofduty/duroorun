"""한국교통안전공단 주차정보 시드 스크립트 (편의시설 - 주차장).

ㅡ **운영(배포) 서버에서는 안 씀, 스케줄러 미등록** (parking.py 참고)
ㅡ 코드는 지우지 않고 남겨둠 - 나중에 이 API가 안정화되면 지금 방식보다
  더 정확한 데이터로 채울 수 있고, 실패해도 기존 데이터를 해치지 않음
  (facilities.is_admin_edited 락 + 재시도/스킵 로직으로 안전하게 설계).
ㅡ 전국 데이터(약 176만 건)라 페이지네이션으로 전부 훑으면서 강원 지역만 골라 upsert.
ㅡ 전국 훑는 방식도 최선인가 의문, 실제 사용되면 파일 내용 검토 필요(검토 안함)
"""

import asyncio
import logging
import selectors
import sys

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.parking import ParkingAPIError, get_parking_page
from app.database import AsyncSessionLocal, engine
from app.domain.facility.models import Facility, FacilityType

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_PAGE_SIZE = 1000
_MAX_PAGES = 3000  # 전국 176만건 기준 대략 1800페이지 - 여유 있게 상한
_TARGET_REGION = "강원"
# seed_courses.py의 _SEED_LOCK_KEY(727501)와 겹치지 않는 임의의 정수
_SEED_LOCK_KEY = 727502


def _is_target_region(item: dict) -> bool:
    """API 응답 item이 강원 지역 주차장인지 확인 (시도 필드 접두어 기준)."""
    return (item.get("prk_plce_adres_sido") or "").startswith(_TARGET_REGION)


def _map_item(item: dict) -> dict:
    return {
        "facility_type": FacilityType.PARKING,
        "facility_name": item["prk_plce_nm"],
        "facility_address": item.get("prk_plce_adres") or None,
        "latitude": float(item["prk_plce_entrc_la"]),
        "longitude": float(item["prk_plce_entrc_lo"]),
        "external_ref": item["prk_center_id"],
        "is_active": True,
    }


async def _upsert_page(session: AsyncSession, items: list[dict]) -> None:
    """한 페이지 분량의 강원 주차장을 external_ref 기준으로 upsert."""
    rows = []
    for item in items:
        try:
            rows.append(_map_item(item))
        except (KeyError, ValueError, TypeError):
            logger.warning(
                "주차장 필드 매핑 실패, 스킵: prk_center_id=%s", item.get("prk_center_id")
            )
    if not rows:
        return

    # external_ref 중복 있으면 나중값 덮어쓴거 1개만 남기고 INSERT문에 넘기기
    rows = list({row["external_ref"]: row for row in rows}.values())

    stmt = pg_insert(Facility).values(rows)
    update_cols = {
        col: stmt.excluded[col]
        for col in ("facility_name", "facility_address", "latitude", "longitude", "is_active")
    }
    # 관리자가 이 주차장을 직접 수정/비활성화한 적이 있으면(is_admin_edited=true)
    # 이번 재시드로 이름/주소/좌표/is_active를 덮어쓰지 않음 - 관리자 판단이 우선
    # (2026-09 결정, facility/service.py의 sync_nearby_facilities와 동일한 정책)
    stmt = stmt.on_conflict_do_update(
        index_elements=["external_ref"],
        set_=update_cols,
        where=Facility.is_admin_edited.is_(False),
    )
    await session.execute(stmt)
    await session.commit()


_PAGE_INTERVAL_SECONDS = 0.4
# 실측 결과 이 API가 페이지/시간대/응답크기와 무관하게 무작위로 약 20% 확률로
# 응답을 못 줌(2026-09-13~14 확인) - 한 페이지 실패로 전체 스캔(최대 1800여 페이지)을
# 포기하지 않도록, 그 페이지만 몇 번 더 재시도하고 그래도 안 되면 건너뛰고 계속 진행
_PAGE_MAX_ATTEMPTS = 5
_PAGE_RETRY_DELAY_SECONDS = 1.0


async def _fetch_page_with_retry(page_no: int) -> tuple[list[dict], int] | None:
    """한 페이지를 최대 _PAGE_MAX_ATTEMPTS번 시도. 끝까지 실패하면 None(이 페이지 스킵)."""
    for attempt in range(1, _PAGE_MAX_ATTEMPTS + 1):
        try:
            return await get_parking_page(page_no, _PAGE_SIZE)
        except ParkingAPIError:
            if attempt == _PAGE_MAX_ATTEMPTS:
                logger.warning(
                    "주차정보 API 페이지 %d, %d번 재시도 끝까지 실패해 이 페이지는 건너뜀",
                    page_no, _PAGE_MAX_ATTEMPTS, exc_info=True,
                )
                return None
            await asyncio.sleep(_PAGE_RETRY_DELAY_SECONDS)
    return None  # pragma: no cover - 위 루프에서 항상 return/return None으로 빠짐


async def _run(session: AsyncSession) -> None:
    page_no = 1
    fetched_count = 0
    matched_count = 0
    skipped_pages = 0
    total_count = None
    while page_no <= _MAX_PAGES:
        result = await _fetch_page_with_retry(page_no)
        if result is None:
            skipped_pages += 1
            page_no += 1
            await asyncio.sleep(_PAGE_INTERVAL_SECONDS)
            continue

        items, total_count = result
        if not items:
            break
        fetched_count += len(items)

        target_items = [item for item in items if _is_target_region(item)]
        matched_count += len(target_items)
        if target_items:
            await _upsert_page(session, target_items)

        if fetched_count >= total_count:
            break
        page_no += 1
        # 실측 결과 짧은 간격으로 연속 요청하면 응답이 씹히는 경우가 있어 페이지 사이에
        # 짧게 텀을 둠 (재시도로도 상당 부분 커버되지만 예방 차원에서 유지)
        await asyncio.sleep(_PAGE_INTERVAL_SECONDS)
    else:
        logger.warning("페이지 상한(%d)에 도달해 조회를 중단합니다.", _MAX_PAGES)

    if skipped_pages:
        logger.warning(
            "재시도 끝에도 못 받아 건너뛴 페이지 %d개 (페이지당 최대 %d건 강원 데이터 누락 가능)",
            skipped_pages, _PAGE_SIZE,
        )
    logger.info(
        "주차장 시드 완료: 전체 조회 %d건 중 강원 %d건 upsert (건너뛴 페이지 %d개)",
        fetched_count, matched_count, skipped_pages,
    )


async def seed_facilities_parking() -> None:
    """주차장 시드를 실행합니다.

    ㅡ seed_courses()와 동일한 pg_try_advisory_lock 패턴 (다른 lock key)으로
      워커/컨테이너가 여러 개여도 동시에 두 번 실행되지 않게 함.
    """
    lock_conn = await engine.connect()
    lock_conn = await lock_conn.execution_options(isolation_level="AUTOCOMMIT")
    got_lock = (
        await lock_conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": _SEED_LOCK_KEY})
    ).scalar_one()
    if not got_lock:
        logger.warning("다른 프로세스가 이미 주차장 시드를 실행 중이라 이번 실행은 건너뜁니다.")
        await lock_conn.close()
        return

    try:
        async with AsyncSessionLocal() as session:
            try:
                await _run(session)
            except ParkingAPIError:
                logger.exception("주차정보 API 호출 실패, 시드를 중단합니다.")
                raise
            except Exception:
                await session.rollback()
                logger.exception("주차장 시드 중 예상치 못한 오류로 중단합니다.")
                raise
    finally:
        await lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _SEED_LOCK_KEY})
        await lock_conn.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        # psycopg(async)가 Windows 기본 이벤트 루프(ProactorEventLoop)를 지원하지 않음
        asyncio.run(
            seed_facilities_parking(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        asyncio.run(seed_facilities_parking())
