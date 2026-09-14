import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { apiFetch } from '../api';
import Header from '../components/layout/Header';

const CARD_COLORS = ['blue', 'green', 'red'];
const COURSE_TYPE_LABEL = { DRNB: '공식', CUSTOM: '커스텀' };

export default function Home() {
  const [popularCourses, setPopularCourses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [popularRes, statsRes] = await Promise.all([
          apiFetch('/v1/courses/popular?limit=3'),
          apiFetch('/v1/courses/landing-stats'),
        ]);
        if (cancelled) return;
        if (popularRes.ok) setPopularCourses(await popularRes.json());
        if (statsRes.ok) setStats(await statsRes.json());
      } catch {
        // 랜딩페이지 보조 섹션이라 실패해도 조용히 빈 상태로 둠
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const topCourse = popularCourses[0];

  return (
    <main>
      <Header />

      <section className="hero" id="top">
        <div className="route-line route-one" />
        <div className="route-line route-two" />
        <div className="hero-copy">
          <div className="eyebrow"><span />강원을 두루 달리다</div>
          <h1>나답게 달리는 길,<br /><em>두루런</em></h1>
          <p>강원의 바다와 산, 도시 곳곳의 러닝 코스부터 러너들이 직접 만든 특별한 길까지.<br />달리는 동안 주변 관광지도 함께 만나보세요.</p>
          <div className="hero-buttons">
            <Link className="primary-button" to="/courses">코스 둘러보기 <span>→</span></Link>
          </div>
          <div className="quick-stats" aria-label="서비스 통계">
            <div><strong>{stats ? stats.total_courses : '-'}</strong><span>강원 추천 코스</span></div>
            <div><strong>{stats ? stats.total_completions.toLocaleString() : '-'}</strong><span>누적 완주 기록</span></div>
            <div><strong>{stats ? stats.total_reviews.toLocaleString() : '-'}</strong><span>누적 리뷰 수</span></div>
          </div>
        </div>

        <div className="hero-visual" aria-label="강원을 달리는 두루미 캐릭터">
          <div className="sun" />
          <div className="mountain mountain-back" />
          <div className="mountain mountain-front" />
          <div className="sea"><i /><i /><i /></div>
          <img className="crane" src="/assets/durumi.png" alt="두루런 대표 캐릭터 두루미" />
          <div className="crane-message"><span>오늘도</span><strong>함께 달려요!</strong></div>
          {topCourse && (
            <div className="location-pill">
              <span className="pin">●</span>
              <div><small>지금 인기 있는 코스</small><strong>{topCourse.course_name}</strong></div>
            </div>
          )}
        </div>
      </section>

      <section className="discovery" id="courses">
        <div className="section-heading">
          <div><span className="section-kicker">지금 인기 있는 코스</span><h2>어떤 길을 달려볼까요?</h2></div>
          <Link to="/courses">전체 코스 보기 <span>→</span></Link>
        </div>
        {loading && <p className="course-list-status">불러오는 중...</p>}
        {!loading && popularCourses.length === 0 && (
          <p className="course-list-status">아직 완주 기록이 없어요. 첫 번째 완주자가 되어보세요!</p>
        )}
        {!loading && popularCourses.length > 0 && (
          <div className="course-grid">
            {popularCourses.map((course, index) => (
              <Link
                className={`course-card ${CARD_COLORS[index % CARD_COLORS.length]}`}
                key={course.course_id}
                to={`/courses/${course.course_type.toLowerCase()}/${course.course_id}`}
              >
                <div className="course-art">
                  <span className="course-number">0{index + 1}</span>
                  <div className="mini-route" />
                  <span className="course-badge">{COURSE_TYPE_LABEL[course.course_type] ?? course.course_type}</span>
                </div>
                <div className="course-info">
                  <span>{course.completion_count}회 완주</span>
                  <h3>{course.course_name}</h3>
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>

      <section className="how" id="how">
        <div><span className="section-kicker">두루런 사용법</span><h2>길을 찾고, 달리고,<br />기록을 남겨요.</h2></div>
        <div className="steps">
          <div><b>01</b><strong>코스 발견</strong><span>내게 맞는 강원 코스를 찾아요</span></div>
          <div><b>02</b><strong>러닝 시작</strong><span>AI 날씨·안전 브리핑과 함께 달려요</span></div>
          <div><b>03</b><strong>완주 인증</strong><span>나만의 발자국을 남겨요</span></div>
        </div>
      </section>
    </main>
  );
}
