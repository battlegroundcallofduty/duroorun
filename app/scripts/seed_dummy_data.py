"""공모전 데모용 더미 데이터 일회성 시딩 스크립트.

ㅡ 배포 서버용 명령어:
docker compose exec backend python -m app.scripts.seed_dummy_data
ㅡ 도커 아닌 로컬용 명령어: python -m app.scripts.seed_dummy_data
ㅡ admin 계정(닉네임 "kitty")은 제외. 나머지 7개 계정에 완주 기록/리뷰를 배분해서
  지정한 DRNB 코스 3개("해파랑길 30/31/41코스")와 CUSTOM 코스 3개에 데모용 완주
  기록을 채운다. 완주 횟수를 30>31>41코스 순으로 넣어두지만, 이 스크립트가 건드리지
  않는 다른 코스에 이미 완주 기록이 더 많으면 실제 인기 코스 랭킹은 달라질 수 있다 -
  마지막에 실제 랭킹 조회와 동일한 방식으로 최종 순위를 로그로 찍어서 확인한다
  (코드리뷰 반영: 목표 코스들끼리의 건수 비교만으론 "1위 보장"이라 할 수 없음).
ㅡ CUSTOM 코스가 아직 하나도 없어서, 실제 서비스 로직(create_course)을 그대로 호출해
  3개 새로 만듦 (강원도 경계 검증/시군 계산/편의시설 동기화까지 정상 처리됨).
ㅡ 목표 완주 건수는 "기존 완주 기록 + 이번에 추가하는 건수"의 합계 기준.
  기존에 이미 완주 기록이 있으면 그만큼 덜 추가해서 목표 총합을 맞춘다
  (코드리뷰 반영: 무작위 배분이 기존 기록을 고려 안 하면 목표 순위가 어긋날 수 있음).
ㅡ CUSTOM 코스는 (이름, 제작자) 기준으로 기존 코스를 먼저 찾고 없을 때만 생성 -
  도중에 실패해도 재실행 시 이미 만든 코스는 재사용하고 이어서 완주 기록만 채운다.
  동일 (이름, 제작자) 조합이 여러 개 나오면 어느 걸 재사용할지 알 수 없으므로
  예외를 던지고 중단한다 (코드리뷰 반영: 이름만으로 찾으면 다른 유저가 만든
  동명의 코스를 잘못 재사용할 위험이 있음).
"""

import asyncio
import logging
import random
import selectors
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

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
# must_include: 이 코스엔 반드시 포함시킬 유저(개인 기록용) - 목표 건수 안에서 배분되므로
# 순위에 영향 주지 않음 (기존처럼 목표 건수 밖에서 추가로 얹지 않음)
DRNB_TARGETS = [
    {"course_name": "해파랑길 30코스", "completions": 6, "must_include": []},
    {"course_name": "해파랑길 31코스", "completions": 4, "must_include": []},
    {"course_name": "해파랑길 41코스", "completions": 3, "must_include": ["키티"]},
]

# --- 새로 만들 CUSTOM 코스 (제작자 닉네임, 완주 횟수) ---
CUSTOM_COURSES = [
    {
        "creator_nickname": "바나낭우유",
        "completions": 5,
        "must_include": ["키티구글"],
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
        "must_include": [],
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
        "must_include": [],
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


async def _count_completions(session, course_id: int) -> int:
    result = await session.execute(
        select(func.count(Record.record_id)).where(
            Record.course_id == course_id, Record.is_completed.is_(True)
        )
    )
    return result.scalar_one()


async def _get_or_create_custom_course(session, users: dict[str, User], spec: dict) -> Course:
    """(이름, 제작자) 기준으로 기존 CUSTOM 코스를 먼저 찾고, 없을 때만 새로 만든다.

    ㅡ create_course()는 내부에서 즉시 커밋하므로, 중간에 실패해도 앞서 만든 코스는
      DB에 남는다. 재실행 시 이미 만든 코스를 중복 생성하지 않고 재사용하기 위함
      (코드리뷰 반영: 부분 실패 후 재실행하면 CUSTOM 코스가 중복 생성되는 문제).
    ㅡ 이름만으로 찾으면 다른 유저가 우연히 같은 이름으로 만든 실제 코스를 잘못
      재사용할 수 있어 created_by까지 조건에 넣는다. 그래도 동일 (이름, 제작자)가
      여러 개 나오면 어떤 걸 재사용할지 자동으로 판단하지 않고 예외로 중단한다
      (코드리뷰 반영).
    """
    course_name = spec["request"].course_name
    creator = users[spec["creator_nickname"]]
    existing = await session.execute(
        select(Course).where(
            Course.course_name == course_name,
            Course.course_type == CourseType.CUSTOM,
            Course.created_by == creator.user_id,
        )
    )
    matches = list(existing.scalars().all())
    if len(matches) > 1:
        ids = [c.course_id for c in matches]
        raise RuntimeError(
            f"'{course_name}'(제작자={spec['creator_nickname']}) 이름의 CUSTOM 코스가 "
            f"이미 여러 개 있습니다 (course_id={ids}). 어느 코스를 데모용으로 쓸지 "
            "직접 확인하고 스크립트에서 course_id를 지정해주세요."
        )
    if matches:
        course = matches[0]
        logger.info("CUSTOM 코스 이미 존재, 재사용: %s (course_id=%s)", course_name, course.course_id)
        return course

    response = await create_course(session, creator.user_id, spec["request"])
    course = await session.get(Course, response.course_id)
    logger.info(
        "CUSTOM 코스 생성 완료: %s (course_id=%s, 제작자=%s)",
        course.course_name,
        course.course_id,
        spec["creator_nickname"],
    )
    return course


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        users = await _get_users_by_nickname(session, NICKNAMES)
        drnb_courses = await _get_courses_by_name(
            session, [target["course_name"] for target in DRNB_TARGETS]
        )

        # 1) CUSTOM 코스 3개 준비 (이미 있으면 재사용, 없으면 생성)
        custom_courses: list[Course] = []
        for spec in CUSTOM_COURSES:
            custom_courses.append(await _get_or_create_custom_course(session, users, spec))

        nickname_cycle = list(users.keys())
        random.shuffle(nickname_cycle)

        def _pick_nicknames(n: int, exclude: set[str] | None = None) -> list[str]:
            if n <= 0:
                return []
            pool = [nn for nn in nickname_cycle if nn not in (exclude or set())]
            if not pool:
                return []
            if len(pool) < n:
                pool = pool * ((n // len(pool)) + 1)
            return random.sample(pool, n) if len(pool) >= n else pool[:n]

        # 대상 유저가 대상 코스에 이미 써둔 리뷰가 있으면(예: 팀원이 개발 중 직접 테스트로
        # 남긴 리뷰) uq_reviews_course_user 위반으로 커밋 전체가 실패하므로 미리 걸러낸다.
        target_user_ids = [u.user_id for u in users.values()]
        all_course_ids = [c.course_id for c in drnb_courses.values()] + [
            c.course_id for c in custom_courses
        ]
        existing_review_rows = await session.execute(
            select(Review.course_id, Review.user_id).where(
                Review.course_id.in_(all_course_ids), Review.user_id.in_(target_user_ids)
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

        async def _allocate_completions(
            course: Course, target_total: int, must_include: list[str], *, base_days_ago: int
        ) -> None:
            """course에 완주 기록을 추가해서 총 완주 건수가 target_total이 되도록 맞춘다.

            ㅡ 기존 완주 기록 수를 먼저 세서 그만큼 빼고 추가한다 - 안 그러면 실행마다
              (또는 재실행 시) 목표보다 많아져 순위가 어긋날 수 있음(코드리뷰 반영).
            ㅡ must_include(개인 기록용 유저)는 목표 건수 "안에서" 배분한다 - 목표 밖에
              추가로 얹으면 다른 코스와 동률/역전이 날 수 있어서(코드리뷰 반영).
            """
            existing_count = await _count_completions(session, course.course_id)
            needed = target_total - existing_count
            if needed <= 0:
                logger.warning(
                    "%s: 이미 완주 기록이 %d건 있어 목표(%d건)를 넘어 추가하지 않음",
                    course.course_name,
                    existing_count,
                    target_total,
                )
                return

            must_include = [nn for nn in must_include if nn in users][:needed]
            rest = _pick_nicknames(needed - len(must_include), exclude=set(must_include))
            selected = must_include + rest

            reviewer_count = min(2, len(selected))
            for i, nickname in enumerate(selected):
                records.append(
                    _make_record(
                        user_id=users[nickname].user_id,
                        course=course,
                        days_ago=base_days_ago + i * 3,
                    )
                )
                if i < reviewer_count:
                    _add_review(course, nickname)

        # 2) DRNB 인기 코스 3개에 완주 기록 배분 (요청한 순위대로 개수 차등)
        for target in DRNB_TARGETS:
            await _allocate_completions(
                drnb_courses[target["course_name"]],
                target["completions"],
                target["must_include"],
                base_days_ago=3,
            )

        # 3) CUSTOM 코스 3개에 완주 기록 배분
        for spec, course in zip(CUSTOM_COURSES, custom_courses, strict=True):
            await _allocate_completions(
                course, spec["completions"], spec["must_include"], base_days_ago=2
            )

        session.add_all(records)
        session.add_all(reviews)
        await session.commit()

        logger.info("완주 기록 %d건, 리뷰 %d건 생성 완료", len(records), len(reviews))

        # 4) 대상 코스들의 최종 건수 (목표치를 넘지 않고 잘 채워졌는지만 확인 - 순위 보장 아님)
        logger.info("=== 대상 코스 최종 완주 건수 ===")
        for target in DRNB_TARGETS:
            course = drnb_courses[target["course_name"]]
            total = await _count_completions(session, course.course_id)
            logger.info("%s: %d건 (목표 %d건)", course.course_name, total, target["completions"])
        for spec, course in zip(CUSTOM_COURSES, custom_courses, strict=True):
            total = await _count_completions(session, course.course_id)
            logger.info("%s: %d건 (목표 %d건)", course.course_name, total, spec["completions"])

        # 5) 진짜 인기 코스 랭킹 재현 (get_popular_courses와 동일한 정렬 기준) -
        # 대상 코스 외에 완주가 더 많은 코스가 있으면 여기서 드러난다.
        await _log_real_ranking(session, CourseType.DRNB)
        await _log_real_ranking(session, CourseType.CUSTOM)


async def _log_real_ranking(session, course_type: CourseType, top_n: int = 5) -> None:
    review_count_subquery = (
        select(func.count(Review.review_id))
        .where(Review.course_id == Course.course_id)
        .correlate(Course)
        .scalar_subquery()
    )
    query = (
        select(
            Course.course_id,
            Course.course_name,
            func.count(Record.record_id).label("completion_count"),
        )
        .join(Record, Record.course_id == Course.course_id)
        .where(Record.is_completed.is_(True), Course.course_type == course_type)
        .group_by(Course.course_id, Course.course_name)
        .order_by(
            func.count(Record.record_id).desc(),
            review_count_subquery.desc(),
            Course.course_id.asc(),
        )
        .limit(top_n)
    )
    rows = (await session.execute(query)).all()
    logger.info("=== 실제 인기 코스 랭킹 재현 (%s, 완주 횟수 기준 상위 %d) ===", course_type, top_n)
    for rank, row in enumerate(rows, start=1):
        logger.info(
            "%d위: %s (course_id=%s, 완주 %d건)",
            rank,
            row.course_name,
            row.course_id,
            row.completion_count,
        )


if __name__ == "__main__":
    if sys.platform == "win32":
        # psycopg(async)가 Windows 기본 이벤트 루프(ProactorEventLoop)를 지원하지 않음
        asyncio.run(seed(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        asyncio.run(seed())
