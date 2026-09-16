"""관리자 대시보드 - API 엔드포인트 (APIRouter)."""

from fastapi import APIRouter, Depends, Query, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_admin
from app.database import get_db
from app.domain.admin import service as admin_service
from app.domain.admin.schemas import (
    BannedAccountListResponse,
    DashboardStatsResponse,
    ForceWithdrawRequest,
    UserSearchListResponse,
)
from app.domain.review import service as review_service
from app.domain.review.schemas import MyReviewListResponse
from app.domain.user.models import User
from app.domain.user.schemas import MessageResponse
from app.redis import get_redis

router = APIRouter(prefix="/admin", tags=["admin"])


@router.delete("/users/{user_id}", response_model=MessageResponse, summary="유저 강제 탈퇴")
async def force_withdraw(
    user_id: int,
    body: ForceWithdrawRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    admin: User = Depends(get_current_admin),
) -> MessageResponse:
    """지속적으로 문제가 되는 유저를 강제 탈퇴 처리합니다."""
    await admin_service.force_withdraw_user(admin.user_id, user_id, body.reason, db, redis)
    return MessageResponse(message="유저가 강제 탈퇴 처리되었습니다")


@router.get("/users/search", response_model=UserSearchListResponse, summary="닉네임으로 유저 검색")
async def search_users(
    nickname: str = Query(min_length=1, max_length=30),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> UserSearchListResponse:
    """닉네임 일부로 유저를 검색합니다 (관리자 계정 제외). 강제 탈퇴 대상을 찾을 때 사용."""
    return await admin_service.search_users(nickname, page, size, db)


@router.get(
    "/users/{user_id}/reviews",
    response_model=MyReviewListResponse,
    summary="유저 작성 리뷰 목록 조회",
)
async def get_user_reviews(
    user_id: int,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> MyReviewListResponse:
    """관리자가 특정 유저가 작성한 리뷰 목록을 조회합니다 (부적절한 리뷰 삭제 대상 확인용).

    리뷰 도메인의 get_my_reviews를 그대로 재사용 - 원래도 임의의 user_id를 받을 수
    있게 되어 있었고(마이페이지 라우터에서만 본인 것으로 고정해 호출), 로직 중복 없이
    그대로 쓸 수 있다. 삭제는 기존 DELETE /reviews/{review_id}가 이미 관리자를 허용한다.
    """
    return await review_service.get_my_reviews(session=db, user_id=user_id, page=page, size=size)


@router.get("/banned-accounts", response_model=BannedAccountListResponse, summary="밴 목록 조회")
async def get_banned_accounts(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> BannedAccountListResponse:
    """강제 탈퇴로 재가입이 막힌 소셜 계정 목록을 조회합니다."""
    return await admin_service.get_banned_accounts(page, size, db)


@router.delete(
    "/banned-accounts/{banned_id}", status_code=status.HTTP_204_NO_CONTENT, summary="밴 해제"
)
async def unban_account(
    banned_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> None:
    """밴을 해제하여 해당 소셜 계정으로 재가입할 수 있게 합니다."""
    await admin_service.unban_account(banned_id, db)


@router.get("/dashboard", response_model=DashboardStatsResponse, summary="대시보드 통계 조회")
async def get_dashboard(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
) -> DashboardStatsResponse:
    """유저/러닝기록/코스/리뷰/편의시설 통계를 조회합니다."""
    return await admin_service.get_dashboard_stats(db)
