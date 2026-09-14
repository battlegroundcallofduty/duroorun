"""편의시설 - Pydantic 스키마 (요청/응답 검증)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.facility.models import FacilityType


class FacilityCreateRequest(BaseModel):
    """편의시설 등록 - 관리자 전용"""

    facility_type: FacilityType
    facility_name: str = Field(min_length=1)
    facility_address: str | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    kakao_place_id: str | None = None
    # 연결할 코스 (선택사항, 복수 선택 가능)
    course_ids: list[int] = Field(default_factory=list)
    # 코스 id 안보내면, list() 실행 → 매번 새로운 [] 생성


class FacilityUpdateRequest(BaseModel):
    """편의시설 수정 - 관리자 전용. 부분 수정이므로 전달된 필드만 반영"""

    facility_type: FacilityType | None = None
    facility_name: str | None = Field(default=None, min_length=1)
    facility_address: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    kakao_place_id: str | None = None
    # None이면 코스 연결 변경 없음, 빈 리스트면 전체 연결 해제
    course_ids: list[int] | None = None
    # 삭제(is_active=false) 처리된 시설을 관리자가 되돌릴 때 사용. None이면 변경 없음
    is_active: bool | None = None


class FacilityCourseOverrideRequest(BaseModel):
    """특정 코스에서 이 시설을 강제 포함/제외 - 반경 자동 매칭의 예외 처리용.

    ㅡ is_excluded=False: 반경 밖이어도 이 코스에 강제로 노출
    ㅡ is_excluded=True: 반경 안이어도 이 코스에서는 숨김
    """

    is_excluded: bool


class FacilityResponse(BaseModel):
    """편의시설 조회 시 응답"""

    model_config = ConfigDict(from_attributes=True)

    facility_id: int
    facility_type: FacilityType
    facility_name: str
    facility_address: str | None
    latitude: float
    longitude: float
    kakao_place_id: str | None
    place_url: str | None
    is_active: bool
    # True면 관리자가 수동 등록 또는 비활성화한 상태
    # 재시드가 이 시설을 되살리지 않음. 다시 활성화하면 풀림.
    # (이름/주소/좌표 고치는 단순 수정은 잠그지 않음)
    is_admin_edited: bool
    created_at: datetime
    updated_at: datetime | None


class FacilityListResponse(BaseModel):
    """편의시설 목록 조회 시 응답 - offset 페이지네이션"""

    items: list[FacilityResponse]
    total: int
    page: int
    size: int
