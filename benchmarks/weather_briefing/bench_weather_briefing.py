"""AI 코스 날씨·안전 브리핑 캐시 효과 측정 스크립트.

측정 대상: GET /api/v1/courses/{course_id}/weather-briefing
측정 방법:
  1. 코스마다 Redis의 날씨 캐시(weather_* 키)를 전부 지워 완전한 콜드 상태를 만든 뒤
  2. 1번째 호출 = 캐시 미스 (기상청 단기예보 + 특보현황 + Gemini 호출 포함)
  3. 2~4번째 호출 = 캐시 히트 (외부 API 호출 없이 Redis에서 응답)
  4. 지역(기상청 격자)이 서로 다른 코스 4개로 반복

※ 로컬 전용. 로컬 Redis의 weather_* 키만 지우며, 운영 서버에는 절대 실행하지 말 것.
※ 날씨 브리핑은 캐시 미스일 때만 IP당 시간당 30회 rate limit에 집계됨
   (이 스크립트는 1회 실행에 미스 4회만 발생).

실행 (프로젝트 루트에서, 로컬 uvicorn + docker compose redis 실행 중인 상태):
  python benchmarks/weather_briefing/bench_weather_briefing.py
"""

import asyncio
import platform
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from redis.asyncio import Redis

BASE_URL = "http://localhost:8000/api/v1"
REDIS_URL = "redis://127.0.0.1:6379/0"
CACHE_KEY_PATTERN = "weather_*"
CALLS_PER_COURSE = 4

# 지역(기상청 5km 격자)이 서로 다르도록 고른 코스 4개
TARGET_COURSES = [
    "해파랑길 30코스",
    "해파랑길 45코스",
    "DMZ 평화의 길 18코스",
    "DMZ 평화의 길 25코스",
]

RESULT_DIR = Path(__file__).parent
KST = ZoneInfo("Asia/Seoul")


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


async def _resolve_course_ids(client: httpx.AsyncClient) -> list[tuple[int, str, str]]:
    """코스명으로 course_id를 찾는다 (DB마다 id가 달라서 이름 기준으로 조회)."""
    resp = await client.get(f"{BASE_URL}/courses/drnb", params={"size": 100})
    resp.raise_for_status()
    items = resp.json()["items"]

    found = []
    for target in TARGET_COURSES:
        match = next((c for c in items if c["course_name"].startswith(target)), None)
        if match is None:
            sys.exit(f"코스를 찾지 못했습니다: {target}")
        found.append((match["course_id"], match["course_name"], match["sigun"] or "-"))
    return found


async def _clear_weather_cache(redis: Redis) -> int:
    keys = [key async for key in redis.scan_iter(match=CACHE_KEY_PATTERN)]
    if keys:
        await redis.delete(*keys)
    return len(keys)


async def _timed_get(client: httpx.AsyncClient, url: str) -> tuple[float, int, dict | None]:
    start = time.perf_counter()
    resp = await client.get(url)
    elapsed_ms = (time.perf_counter() - start) * 1000
    body = resp.json() if resp.status_code == 200 else None
    return elapsed_ms, resp.status_code, body


def _fmt_ms(ms: float) -> str:
    return f"{ms / 1000:.2f}초" if ms >= 1000 else f"{ms:.0f}ms"


async def main() -> None:
    now = datetime.now(KST)
    lines: list[str] = []

    def log(text: str = "") -> None:
        print(text)
        lines.append(text)

    log("# AI 코스 날씨·안전 브리핑 캐시 효과 측정")
    log(f"측정 시각  : {now:%Y-%m-%d %H:%M:%S} KST")
    log("측정 환경  : 로컬 (uvicorn + docker compose Redis), " + platform.platform())
    log(f"커밋       : {_git_commit()}")
    log(f"엔드포인트 : GET {BASE_URL}/courses/{{course_id}}/weather-briefing")
    log(f"방법       : 코스마다 Redis '{CACHE_KEY_PATTERN}' 키 삭제 후 {CALLS_PER_COURSE}회 연속 호출")
    log("             (1회차 = 캐시 미스, 2회차 이후 = 캐시 히트)")
    log()

    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    rows = []
    has_warning = False
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            courses = await _resolve_course_ids(client)
            for course_id, name, sigun in courses:
                deleted = await _clear_weather_cache(redis)
                url = f"{BASE_URL}/courses/{course_id}/weather-briefing"
                timings = []
                for i in range(CALLS_PER_COURSE):
                    ms, status_code, body = await _timed_get(client, url)
                    timings.append(ms)
                    log(
                        f"[{name}] {i + 1}회차 {'미스' if i == 0 else '히트'}: "
                        f"{_fmt_ms(ms)} (HTTP {status_code}, 캐시 키 {deleted if i == 0 else 0}개 삭제)"
                    )
                    if status_code != 200:
                        sys.exit(f"HTTP {status_code} 응답으로 측정 중단: {url}")
                    if body and body.get("warning_raw_text"):
                        has_warning = True
                rows.append((name, sigun, timings[0], timings[1:]))
    finally:
        await redis.aclose()

    log()
    log("## 결과")
    log()
    log("| 코스 | 지역 | 캐시 미스 (1회차) | 캐시 히트 (2~4회차) |")
    log("|------|------|------:|------:|")
    for name, sigun, miss, hits in rows:
        log(f"| {name} | {sigun} | {_fmt_ms(miss)} | {_fmt_ms(min(hits))} ~ {_fmt_ms(max(hits))} |")

    misses = [r[2] for r in rows]
    all_hits = [h for r in rows for h in r[3]]
    log()
    log(f"- 캐시 미스: {_fmt_ms(min(misses))} ~ {_fmt_ms(max(misses))}")
    log(f"- 캐시 히트: {_fmt_ms(min(all_hits))} ~ {_fmt_ms(max(all_hits))}")
    log(f"- 보수적 비교(가장 빠른 미스 / 가장 느린 히트): 약 {min(misses) / max(all_hits):.0f}배")
    log(
        "- 측정 시점 강원 특보: "
        + ("발효 중 (특보-지역 Gemini 코멘트 호출 포함)" if has_warning else "없음 (특보-지역 Gemini 코멘트 호출 없음)")
    )

    out_path = RESULT_DIR / f"result_{now:%Y-%m-%d_%H%M}.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n결과 저장: {out_path}")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
