"""편의시설 - API 엔드포인트 (APIRouter)."""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_admin
from app.database import get_db
from app.domain.facility import service as facility_service
from app.domain.facility.models import FacilityType
from app.domain.facility.schemas import (
    FacilityCourseOverrideRequest,
    FacilityCreateRequest,
    FacilityListResponse,
    FacilityResponse,
    FacilityUpdateRequest,
)
from app.domain.user.models import User

router = APIRouter(prefix="/facilities", tags=["facilities"])


@router.post("", response_model=FacilityResponse, status_code=status.HTTP_201_CREATED)
async def create_facility(
    body: FacilityCreateRequest,
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """편의시설 등록 (관리자 전용)"""
    return await facility_service.create_facility(session=session, body=body)


@router.get("", response_model=FacilityListResponse)
async def get_facilities(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    course_id: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_db),
):
    """편의시설 목록 조회 (course_id 전달 → 해당 코스 주변 시설만 조회)"""
    return await facility_service.get_facilities(
        session=session, page=page, size=size, course_id=course_id
    )


@router.get("/admin", response_model=FacilityListResponse)
async def get_facilities_for_admin(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    facility_type: FacilityType | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    keyword: str | None = Query(default=None, min_length=1, max_length=100),
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """편의시설 목록 조회 (관리자 전용, is_active 무관 전체 노출).
    is_active/keyword를 넘기면 그 상태/시설명만 필터링."""
    return await facility_service.get_facilities_for_admin(
        session=session,
        page=page,
        size=size,
        facility_type=facility_type,
        is_active=is_active,
        keyword=keyword,
    )


@router.get("/{facility_id}", response_model=FacilityResponse)
async def get_facility(facility_id: int, session: AsyncSession = Depends(get_db)):
    """편의시설 상세 조회"""
    return await facility_service.get_facility(session=session, facility_id=facility_id)


@router.patch("/{facility_id}", response_model=FacilityResponse)
async def update_facility(
    facility_id: int,
    body: FacilityUpdateRequest,
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """편의시설 수정 (관리자 전용)"""
    return await facility_service.update_facility(
        session=session, facility_id=facility_id, body=body
    )


@router.delete("/{facility_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_facility(
    facility_id: int,
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """편의시설 삭제 (관리자 전용, Soft Delete)"""
    await facility_service.delete_facility(session=session, facility_id=facility_id)


@router.put("/{facility_id}/courses/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def set_facility_course_override(
    facility_id: int,
    course_id: int,
    body: FacilityCourseOverrideRequest,
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """특정 코스에서 이 시설을 강제 포함/제외 (반경 매칭의 예외 처리, 관리자 전용)"""
    await facility_service.set_course_facility_override(
        session=session,
        facility_id=facility_id,
        course_id=course_id,
        is_excluded=body.is_excluded,
    )


@router.delete("/{facility_id}/courses/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_facility_course_override(
    facility_id: int,
    course_id: int,
    session: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """포함/제외 override 제거 - 반경 자동 판정으로 되돌림 (관리자 전용)"""
    await facility_service.clear_course_facility_override(
        session=session, facility_id=facility_id, course_id=course_id
    )
