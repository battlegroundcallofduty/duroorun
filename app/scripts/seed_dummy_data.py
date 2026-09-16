"""공모전 데모용 더미 데이터 일회성 시딩 스크립트.

ㅡ 배포 서버용 명령어:
docker compose exec backend python -m app.scripts.seed_dummy_data
ㅡ 도커 아닌 로컬용 명령어: python -m app.scripts.seed_dummy_data
ㅡ admin 계정(닉네임 "kitty")은 제외. 나머지 7개 계정에 완주 기록/리뷰를 배분해서
  인기 코스(완주 횟수 기준) 랭킹에 "해파랑길 30코스"가 DRNB 1위로 뜨도록 맞춤.
ㅡ CUSTOM 코스가 아직 하나도 없어서, 실제 서비스 로직(create_course)을 그대로 호출해
  3개 새로 만듦 (강원도 경계 검증/시군 계산/편의시설 동기화까지 정상 처리됨).
ㅡ 여러 번 실행하면 중복 생성됨 (idempotent 아님) - 한 번만 실행할 것.
"""

import asyncio
import logging
import random
import selectors
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.domain.course.models import Course, CourseType, Difficulty
from app.domain.course.schemas import CourseCreateRequest, CourseWaypointCreate
from app.domain.course.service import create_course
from app.domain.record.models import Record
from app.domain.review.models import Review
from app.domain.user.models import User

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --- 대상 유저 (관리자 계정 "kitty"는 제외) ---
NICKNAMES = ["바나낭우유", "키티", "망고마라탕", "1번", "딸기설렁탕", "바나낭우유지", "키티구글"]

# --- 인기 코스 1/2/3위로 만들 기존 DRNB 코스 (완주 횟수 내림차순) ---
DRNB_TARGETS = [
    ("해파랑길 30코스", 6),
    ("해파랑길 31코스", 4),
    ("해파랑길 41코스", 3),
]

# --- 새로 만들 CUSTOM 코스 (제작자 닉네임, 완주 횟수) ---
CUSTOM_COURSES = [
    {
        "creator_nickname": "바나낭우유",
        "completions": 5,
        "request": CourseCreateRequest(
            course_name="소양강 러닝 코스",
            distance=5.2,
            difficulty=Difficulty.NORMAL,
            estimated_time=35,
            course_description="춘천 소양강을 따라 달리는 강변 코스입니다.",
            waypoints=[
                CourseWaypointCreate(latitude=37.8813, longitude=127.7300),
                CourseWaypointCreate(latitude=37.8760, longitude=127.7420),
                CourseWaypointCreate(latitude=37.8710, longitude=127.7550),
            ],
        ),
    },
    {
        "creator_nickname": "망고마라탕",
        "completions": 3,
        "request": CourseCreateRequest(
            course_name="경포호 한바퀴",
            distance=8.0,
            difficulty=Difficulty.HARD,
            estimated_time=55,
            course_description="강릉 경포호를 크게 한 바퀴 도는 코스입니다.",
            waypoints=[
                CourseWaypointCreate(latitude=37.7961, longitude=128.8971),
                CourseWaypointCreate(latitude=37.8010, longitude=128.9090),
                CourseWaypointCreate(latitude=37.8030, longitude=128.9050),
            ],
        ),
    },
    {
        "creator_nickname": "딸기설렁탕",
        "completions": 2,
        "request": CourseCreateRequest(
            course_name="속초 해변 러닝",
            distance=3.1,
            difficulty=Difficulty.EASY,
            estimated_time=20,
            course_description="속초 해수욕장을 따라 가볍게 달리는 코스입니다.",
            waypoints=[
                CourseWaypointCreate(latitude=38.1990, longitude=128.5945),
                CourseWaypointCreate(latitude=38.2100, longitude=128.5900),
            ],
        ),
    },
]

REVIEW_CONTENTS = [
    "경치가 정말 좋아서 힘든 줄 모르고 달렸어요. 다음에 또 올 거예요!",
    "초반 오르막이 좀 있어서 생각보다 힘들었지만 완주하고 나니 뿌듯하네요.",
    "가족이랑 산책하듯 가볍게 뛰기 좋은 코스였습니다. 추천해요.",
    "노을 질 때 달리니까 진짜 예뻤어요. 사진 찍느라 페이스가 좀 느려졌네요 ㅎㅎ",
    "편의시설이 잘 되어있어서 화장실 걱정 없이 달릴 수 있었어요.",
    "바람이 시원하게 불어서 여름에 달리기 딱 좋은 코스예요.",
    "생각보다 거리가 있어서 초보자는 조금 힘들 수 있어요. 그래도 완주 성취감이 커요.",
]


async def _get_users_by_nickname(session, nicknames: list[str]) -> dict[str, User]:
    result = await session.execute(select(User).where(User.nickname.in_(nicknames)))
    users = {u.nickname: u for u in result.scalars().all()}
    missing = set(nicknames) - set(users)
    if missing:
        raise RuntimeError(f"닉네임을 찾을 수 없습니다: {missing}")
    return users


async def _get_courses_by_name(session, names: list[str]) -> dict[str, Course]:
    result = await session.execute(
        select(Course).where(Course.course_name.in_(names), Course.course_type == CourseType.DRNB)
    )
    courses = {c.course_name: c for c in result.scalars().all()}
    missing = set(names) - set(courses)
    if missing:
        raise RuntimeError(f"코스를 찾을 수 없습니다: {missing}")
    return courses


def _make_record(*, user_id: int, course: Course, days_ago: int) -> Record:
    """이미 완주 처리된 과거 기록 1건을 만든다 (실제 러닝 흐름 대신 직접 구성)."""
    distance_km = course.distance or 5.0
    pace_sec_per_km = random.uniform(330, 420)  # 5분30초~7분/km
    duration_seconds = int(pace_sec_per_km * distance_km)
    started_at = datetime.now(UTC) - timedelta(days=days_ago, hours=random.randint(0, 20))
    ended_at = started_at + timedelta(seconds=duration_seconds)
    return Record(
        user_id=user_id,
        course_id=course.course_id,
        duration_seconds=duration_seconds,
        started_at=started_at,
        ended_at=ended_at,
        pace=pace_sec_per_km,
        user_start_lat=course.start_lat,
        user_start_lng=course.start_lng,
        user_end_lat=course.end_lat,
        user_end_lng=course.end_lng,
        is_completed=True,
        distance_km=distance_km,
        total_paused_seconds=0,
    )


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        users = await _get_users_by_nickname(session, NICKNAMES)
        drnb_courses = await _get_courses_by_name(session, [name for name, _ in DRNB_TARGETS])

        # 1) CUSTOM 코스 3개 생성 (실제 서비스 함수 그대로 사용)
        custom_courses: list[Course] = []
        for spec in CUSTOM_COURSES:
            creator = users[spec["creator_nickname"]]
            response = await create_course(session, creator.user_id, spec["request"])
            course = await session.get(Course, response.course_id)
            custom_courses.append(course)
            logger.info(
                "CUSTOM 코스 생성 완료: %s (course_id=%s, 제작자=%s)",
                course.course_name,
                course.course_id,
                spec["creator_nickname"],
            )

        nickname_cycle = list(users.keys())
        random.shuffle(nickname_cycle)

        def _pick_nicknames(n: int, exclude: set[str] | None = None) -> list[str]:
            pool = [nn for nn in nickname_cycle if nn not in (exclude or set())]
            if len(pool) < n:
                pool = pool * ((n // len(pool)) + 1)
            return random.sample(pool, n) if len(pool) >= n else pool[:n]

        # 대상 유저가 대상 코스에 이미 써둔 리뷰가 있으면(예: 팀원이 개발 중 직접 테스트로
        # 남긴 리뷰) uq_reviews_course_user 위반으로 커밋 전체가 실패하므로 미리 걸러낸다.
        target_course_ids = [c.course_id for c in drnb_courses.values()]
        target_user_ids = [u.user_id for u in users.values()]
        existing_review_rows = await session.execute(
            select(Review.course_id, Review.user_id).where(
                Review.course_id.in_(target_course_ids), Review.user_id.in_(target_user_ids)
            )
        )

        records: list[Record] = []
        reviews: list[Review] = []
        used_review_pairs: set[tuple[int, int]] = set(existing_review_rows.all())

        def _add_review(course: Course, nickname: str) -> None:
            user_id = users[nickname].user_id
            key = (course.course_id, user_id)
            if key in used_review_pairs:
                return
            used_review_pairs.add(key)
            reviews.append(
                Review(
                    user_id=user_id,
                    course_id=course.course_id,
                    content=random.choice(REVIEW_CONTENTS),
                    difficulty=course.difficulty or Difficulty.NORMAL,
                )
            )

        # 2) DRNB 인기 코스 3개에 완주 기록 배분 (요청한 순위대로 개수 차등)
        for name, completion_count in DRNB_TARGETS:
            course = drnb_courses[name]
            reviewers = _pick_nicknames(min(2, completion_count))
            for i, nickname in enumerate(reviewers):
                records.append(
                    _make_record(user_id=users[nickname].user_id, course=course, days_ago=3 + i * 4)
                )
                _add_review(course, nickname)
            remaining = completion_count - len(reviewers)
            for i, nickname in enumerate(_pick_nicknames(remaining, exclude=set(reviewers))):
                records.append(
                    _make_record(
                        user_id=users[nickname].user_id, course=course, days_ago=10 + i * 5
                    )
                )

        # 3) CUSTOM 코스 3개에 완주 기록 배분
        for spec, course in zip(CUSTOM_COURSES, custom_courses, strict=True):
            completion_count = spec["completions"]
            reviewers = _pick_nicknames(min(2, completion_count))
            for i, nickname in enumerate(reviewers):
                records.append(
                    _make_record(user_id=users[nickname].user_id, course=course, days_ago=2 + i * 3)
                )
                _add_review(course, nickname)
            remaining = completion_count - len(reviewers)
            for i, nickname in enumerate(_pick_nicknames(remaining, exclude=set(reviewers))):
                records.append(
                    _make_record(user_id=users[nickname].user_id, course=course, days_ago=8 + i * 4)
                )

        # 4) 키티/키티구글 개인 기록 보강 - 완주해본 적 없는 코스 하나씩 더 추가
        extra_targets = [
            ("키티", drnb_courses["해파랑길 41코스"]),
            ("키티구글", custom_courses[0]),
        ]
        for nickname, course in extra_targets:
            user_id = users[nickname].user_id
            if not any(r.user_id == user_id and r.course_id == course.course_id for r in records):
                records.append(_make_record(user_id=user_id, course=course, days_ago=1))
            _add_review(course, nickname)

        session.add_all(records)
        session.add_all(reviews)
        await session.commit()

        logger.info("완주 기록 %d건, 리뷰 %d건 생성 완료", len(records), len(reviews))


if __name__ == "__main__":
    if sys.platform == "win32":
        # psycopg(async)가 Windows 기본 이벤트 루프(ProactorEventLoop)를 지원하지 않음
        asyncio.run(seed(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        asyncio.run(seed())
