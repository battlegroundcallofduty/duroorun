# 두루런 Contributing

팀원 전원이 지켜야 할 Git 작업 방식과 코딩 규칙입니다.

---

## Git 기본 용어

처음 협업하시는 분들을 위한 용어 설명입니다.

| 용어 | 의미 | 예시 |
|------|------|------|
| `origin` | GitHub 원격 저장소의 별명 | `origin` = `https://github.com/...` |
| `feature/user` | 내 컴퓨터(로컬)의 브랜치 | `git checkout feature/user` |
| `origin/main` | GitHub(원격)의 main 브랜치 | `git merge origin/main` |

**origin을 붙이는 기준:**
- **내 컴퓨터에서 이동**할 때 → origin 안 붙임 (`git checkout feature/user`)
- **GitHub의 코드를 참조**할 때 → origin 붙임 (`git merge origin/main`, `git push origin 내브랜치`)

자주 쓰는 명령어:

| 명령어 | 하는 일 |
|--------|---------|
| `git fetch origin` | GitHub에서 최신 정보를 가져옴 (내 코드는 안 바뀜) |
| `git merge origin/main` | GitHub의 main 코드를 내 브랜치에 합침 |
| `git checkout 브랜치명` | 다른 브랜치로 이동 |
| `git status` | 변경된 파일 목록 확인 |
| `git add 파일명` | 커밋할 파일을 지정 |
| `git commit -m "메시지"` | 변경사항을 저장 (커밋) |
| `git push origin 브랜치명` | 내 커밋을 GitHub에 업로드 |

---

## 브랜치 전략

```
main ← user_admin
main ← course_facility
main ← record_review
```

- `main`: 항상 배포 가능한 상태 유지, **직접 push 금지**
- 각자 담당 도메인 브랜치(`user_admin` / `course_facility` / `record_review`)에서만 작업
- main으로는 **PR을 통해서만** 병합

---

## 커밋 메시지 규칙

`[담당 도메인] 작업내용` 형식 — 브랜치명(=담당 도메인)을 접두사로 사용

| 담당 도메인 | 브랜치명 | 예시 |
|------|------|------|
| 회원/인증/관리자/배포 | `user_admin` | `[user_admin] 소셜 로그인 구현` |
| 코스/편의시설 | `course_facility` | `[course_facility] 코스 필터 오류 수정` |
| 기록/리뷰+이미지 | `record_review` | `[record_review] 리뷰 이미지 업로드 로직 개선` |

feat/fix/chore 같은 작업 타입 구분이 필요하면 접두사가 아니라 본문 첫 줄이나 커밋 설명에 자연스럽게 녹여서 쓰거나, `[record_review][fix]`처럼 도메인 뒤에 덧붙입니다. PR 제목도 커밋 메시지와 동일한 형식 사용.

### `git add .` 사용 금지

`git add .`이나 `git add -A`를 사용하면 **본인이 수정하지 않은 파일까지 커밋에 포함**됩니다.

#### 올바른 커밋 순서

```bash
# 1단계: 변경된 파일 목록 확인
git status

# 2단계: 본인이 작업한 파일만 골라서 추가
git add app/domain/course/service.py
git add app/domain/course/router.py

# 3단계: 스테이징된 파일이 내 것만인지 다시 확인
git diff --staged --stat

# 4단계: 커밋
git commit -m "[course_facility] 코스 필터 API 구현"
```

#### 특정 폴더 안의 파일만 추가하고 싶을 때

```bash
git add app/domain/course/
```

#### 실수로 다른 파일까지 add 했을 때

```bash
# 특정 파일을 스테이징에서 제거 (파일 내용은 유지됨)
git restore --staged app/config.py
```

---

## PR 규칙

PR 올리기 전에 반드시 최신 main을 내 브랜치에 반영합니다.

```bash
git fetch origin
git merge origin/main
# 충돌이 있으면 해결 후 커밋
```

- 최소 **1명 리뷰** 후 머지
- PR 제목도 커밋 메시지와 동일한 형식(`[담당 도메인] 작업내용`) 사용
- 머지 후 팀원 전체 `git fetch origin && git merge origin/main` 필수

> ⚠️ **`main`에 머지되는 순간 GitHub Actions가 자동으로 실제 서버에 배포합니다** (`.github/workflows/deploy.yml`: lint → test 통과 시 즉시 배포). "머지 = 배포"이니 신중하게 머지하세요.

---

## 공통 파일 수정 시 팀 공유

아래 파일들은 여러 파트에서 사용하므로, 수정 전에 반드시 팀에 알려주세요.

| 공통 파일 | 역할 |
|-----------|------|
| `app/config.py` | 환경변수 설정 |
| `app/database.py` | DB 연결 관리 |
| `app/redis.py` | Redis 연결 관리 |
| `app/main.py` | FastAPI 앱 진입점 |
| `app/api/v1/router.py` | API 라우터 통합 |
| `requirements.txt` | 패키지 의존성 |
| `frontend/src/App.jsx` | 라우터 설정 (전체 페이지 라우트 관리) |
| `frontend/package.json` | 프론트 패키지 의존성 |

공통 파일 수정이 필요하면:
1. 팀 채팅에 수정 내용 공유
2. **별도 PR로 먼저 머지**
3. 나머지 팀원이 `git fetch origin && git merge origin/main`으로 반영

---

## 전체 작업 흐름 요약

```
작업 시작
  └─ git fetch origin && git merge origin/main   (최신화)
  └─ 코드 작업
  └─ ruff check app/                              (린트 확인)
  └─ git status                                   (변경 파일 확인)
  └─ git add 내파일만                              (본인 파일만 추가)
  └─ git diff --staged --stat                     (스테이징 확인)
  └─ git commit -m "[담당 도메인] 작업내용"          (커밋)
  └─ git fetch origin && git merge origin/main    (PR 전 다시 최신화)
  └─ git push origin 내브랜치                      (푸시)
  └─ GitHub에서 PR 생성 → 팀원 리뷰 → 머지
  └─ 머지 후 전체 팀원 git fetch origin && git merge origin/main
```

---

## Python (백엔드)

### 네이밍
- 함수 / 변수: `snake_case`
- 클래스: `PascalCase`
- 상수: `UPPER_SNAKE_CASE`
- ENUM 값: `UPPER_CASE`

### 타입 힌트
- 모든 함수의 파라미터와 리턴 타입 필수
- Optional은 `str | None` 형식 사용 (`Optional[str]` 사용 금지)

```python
# Good
async def get_course(course_id: int) -> CourseResponse:

# Bad
async def get_course(course_id):
```

### Docstring
- 한글로 작성, 함수 첫 줄에 한 줄 설명

```python
async def get_course(course_id: int) -> CourseResponse:
    """코스 상세 정보를 조회합니다."""
```

### Import 순서
1. 표준 라이브러리
2. 서드파티 패키지
3. 로컬 모듈

그룹 사이에 빈 줄을 넣습니다.

```python
from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.config import settings
```

### 비동기
- 엔드포인트와 서비스 함수는 모두 `async def`

### 에러 처리
- `HTTPException`으로 통일, 상태 코드와 한글 메시지 포함

```python
raise HTTPException(status_code=404, detail="코스를 찾을 수 없습니다.")
```

### 페이지네이션
- 목록 조회는 **offset 페이지네이션**으로 통일 (페이지 번호 방식)
- 쿼리 파라미터: `?page=1&size=20` (page는 1부터 시작)
- 응답에 전체 개수(`total`) 포함하여 프론트가 페이지 수 계산 가능하게

### 매직넘버 상수화
- 이미지 개수/용량 등 정책값은 `config` 상수로 분리 (도메인별로 흩어지지 않게)

```python
REVIEW_IMAGE_MAX_COUNT = 5       # 리뷰 이미지 최대 장수
REVIEW_IMAGE_MAX_SIZE_MB = 5     # 리뷰 이미지 장당 최대 용량(MB)
COURSE_IMAGE_MAX_COUNT = 3       # 커스텀 코스 이미지 최대 장수
COURSE_IMAGE_MAX_SIZE_MB = 5     # 커스텀 코스 이미지 장당 최대 용량(MB)
COMPLETION_RADIUS_M = 300        # 완주 인증 허용 반경(m)
```

### 린터

[Ruff](https://docs.astral.sh/ruff/) 사용, 한 줄 최대 100자. 설정은 `pyproject.toml`에 정의되어 있습니다.

```bash
# 설치
pip install ruff

# 린트 검사
ruff check app/

# 자동 수정
ruff check app/ --fix

# 코드 포맷팅
ruff format app/
```

> **PR 올리기 전 반드시 `ruff check app/` 통과 확인 후 푸시**

---

## React (프론트엔드)

### 네이밍
- 함수 / 변수: `camelCase`
- 컴포넌트: `PascalCase`
- 상수: `UPPER_SNAKE_CASE`
- 파일명: 컴포넌트는 `PascalCase.jsx`, 유틸/훅은 `camelCase.js`

### 스타일
- 문자열: 작은따옴표(`'`) 사용
- 세미콜론: 사용
- 들여쓰기: 스페이스 2칸

### 컴포넌트
- 함수형 컴포넌트 사용 (`class` 컴포넌트 금지)
- `export default`는 파일 맨 아래에

```jsx
// Good
const CourseCard = ({ course }) => {
  return <div>{course.name}</div>;
};

export default CourseCard;
```

### 비동기
- `async/await` 사용 (`.then()` 체이닝 금지)

```javascript
// Good
const data = await fetchCourse(courseId);

// Bad
fetchCourse(courseId).then(data => { ... });
```

### API 호출
- 공통 API 함수 사용 (Access Token을 `Authorization: Bearer` 헤더에 자동 포함)
- 직접 `fetch` 호출 금지
- httpOnly 쿠키(Refresh Token) 전송을 위해 모든 요청에 `credentials: 'include'` 설정 (공통 API 함수에 적용). 백엔드 CORS는 `allow_credentials=True`
- Access Token 만료(401) 시 `/api/v1/auth/refresh`로 재발급 후 원요청 재시도 로직을 공통 함수에 구현

---

## 주석 규칙

- **한글**로 작성
- "왜" 그렇게 했는지를 설명 (코드가 "무엇"을 하는지는 코드 자체로 표현)

```python
# Good: 이유를 설명
# Soft Delete이므로 DB 트리거가 발동하지 않아 서비스 레이어에서 직접 NULL 처리
await session.execute(
    update(Review).where(Review.user_id == user_id).values(user_id=None)
)

# Bad: 코드를 그대로 반복
# user_id를 None으로 업데이트
await session.execute(
    update(Review).where(Review.user_id == user_id).values(user_id=None)
)
```

---

## 보안 유의사항

- `.env` 절대 커밋 금지 (`.gitignore`에 포함). `.env.example`로 필요한 변수 목록만 공유
- JWT: Access Token(30분, sessionStorage) + Refresh Token(14일, httpOnly 쿠키). Refresh는 유저당 1개 저장(Redis `refresh:{user_id}`), 재발급 시 토큰 로테이션
- 로그아웃/탈퇴 시 Access는 Redis 블랙리스트(`blacklist:{access_jti}`) 등록, Refresh는 Redis에서 삭제
- Refresh 쿠키는 `/api/v1/auth/refresh` 경로 한정. `samesite=lax`는 로컬/프로덕션 공통, `secure`만 환경 분기(로컬 `False`, 프로덕션 `True`) — 프론트/API가 완전히 같은 도메인이라 `lax`로 충분함
- CORS 허용 주소 명시 (`*` 사용 금지, 우리 프론트 주소만 허용). httpOnly 쿠키 사용으로 `allow_credentials=True` 필수, 프론트는 `credentials: 'include'`
- 소셜 로그인 OAuth state 검증 필수 (CSRF 방지). state는 Redis(`oauth:state:{provider}:{state}`, TTL 5분) 저장 + 로그인을 시작한 브라우저인지 확인하는 짧은 만료의 `oauth_state` httpOnly 쿠키로 이중 검증
- API 소유권 검증 필수 (본인 리소스만 수정/삭제 가능. `user_id` 검증 챙기기)

---

## 개발 팁

### R2 이미지 삭제 순서
DB 트랜잭션 성공(commit) 후에 R2 삭제 API 호출. 트랜잭션 실패 시 R2 파일만 지워지는 현상 방지.
