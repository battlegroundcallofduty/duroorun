import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import { apiFetch } from '../api';
import Header from '../components/layout/Header';

const CARD_COLORS = ['blue', 'green', 'red'];
const COURSE_TYPE_LABEL = { DRNB: '공식', CUSTOM: '커스텀' };

// 인기 코스 카드 그리드 - 전체/공식/커스텀 섹션에서 공통으로 재사용
const PopularCourseGrid = ({ courses }) => (
  <div className="course-grid">
    {courses.map((course, index) => (
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
          <h3>{course.course_name}</h3>
        </div>
      </Link>
    ))}
  </div>
);

export default function Home() {
  const [popularCourses, setPopularCourses] = useState([]);
  const [drnbCourses, setDrnbCourses] = useState([]);
  const [customCourses, setCustomCourses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [popularRes, drnbRes, customRes, statsRes] = await Promise.all([
          apiFetch('/v1/courses/popular?limit=3'),
          apiFetch('/v1/courses/popular?course_type=DRNB&limit=3'),
          apiFetch('/v1/courses/popular?course_type=CUSTOM&limit=3'),
          apiFetch('/v1/courses/landing-stats'),
        ]);
        if (cancelled) return;
        if (popularRes.ok) setPopularCourses(await popularRes.json());
        if (drnbRes.ok) setDrnbCourses(await drnbRes.json());
        if (customRes.ok) setCustomCourses(await customRes.json());
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

  const [attractions, setAttractions] = useState([]);
  const attractionScrollRef = useRef(null);
  const scrollAttractions = (direction) => {
    const row = attractionScrollRef.current;
    if (!row) return;
    // 카드 실제 너비 + gap(global.css .attraction-scroll-row의 gap:14px)을 동적으로 계산 -
    // CSS에서 카드 크기가 바뀌어도 스크롤 버튼이 카드 경계에 맞게 따라감
    const cardWidth = row.firstElementChild?.offsetWidth ?? 200;
    row.scrollBy({ left: direction * (cardWidth + 14), behavior: 'smooth' });
  };

  useEffect(() => {
    if (!topCourse) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch(`/v1/courses/${topCourse.course_id}/nearby-attractions`);
        if (!res.ok || cancelled) return;
        const data = await res.json();
        if (!cancelled) setAttractions(data.items);
      } catch {
        // 랜딩페이지 보조 섹션이라 실패해도 조용히 빈 목록으로 둠
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [topCourse]);

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
        </div>
        {loading && <p className="course-list-status">불러오는 중...</p>}
        {!loading && popularCourses.length === 0 && (
          <p className="course-list-status">아직 완주 기록이 없어요. 첫 번째 완주자가 되어보세요!</p>
        )}
        {!loading && popularCourses.length > 0 && <PopularCourseGrid courses={popularCourses} />}
      </section>

      {drnbCourses.length > 0 && (
        <section className="discovery discovery-alt">
          <div className="section-heading">
            <div><span className="section-kicker">공식 코스</span><h2>강원도 인기 코스 TOP3</h2></div>
            <Link to="/courses">전체 코스 보기 <span>→</span></Link>
          </div>
          <PopularCourseGrid courses={drnbCourses} />
        </section>
      )}

      {customCourses.length > 0 && (
        <section className="discovery">
          <div className="section-heading">
            <div><span className="section-kicker">커스텀 코스</span><h2>러너들이 직접 만든 인기 코스 TOP3</h2></div>
            <Link to="/courses">전체 코스 보기 <span>→</span></Link>
          </div>
          <PopularCourseGrid courses={customCourses} />
        </section>
      )}

      {attractions.length > 0 && (
        <section className="attraction-highlight">
          <div className="section-heading">
            <div>
              <span className="section-kicker">달리며 만나는 곳</span>
              <h2>{topCourse.course_name} 주변에서 만나는 관광지</h2>
            </div>
            <div className="attraction-heading-actions">
              <div className="attraction-scroll-arrows">
                <button
                  type="button"
                  className="attraction-scroll-arrow"
                  onClick={() => scrollAttractions(-1)}
                  aria-label="이전 관광지"
                >
                  ‹
                </button>
                <button
                  type="button"
                  className="attraction-scroll-arrow"
                  onClick={() => scrollAttractions(1)}
                  aria-label="다음 관광지"
                >
                  ›
                </button>
              </div>
            </div>
          </div>
          <p className="kakao-map-hint-static">
            코스마다 시작·종료 지점 주변 관광지를 실시간으로 추천해드려요. 달리는 동안 여행하듯 강원을 만나보세요.
          </p>
          <div className="attraction-scroll-row" ref={attractionScrollRef}>
            {attractions.map((attraction) => (
              <div key={attraction.content_id ?? attraction.title} className="attraction-card">
                {attraction.image_url ? (
                  <img className="attraction-card-image" src={attraction.image_url} alt={attraction.title} />
                ) : (
                  <div className="attraction-card-image attraction-card-image-empty" aria-hidden="true" />
                )}
                <div className="attraction-card-body">
                  <h3>{attraction.title}</h3>
                  {attraction.address && <p>{attraction.address}</p>}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="how" id="how">
        <div><h2>길을 찾고, 달리고,<br />기록을 남겨요.</h2></div>
        <div className="steps">
          <div><b>01</b><strong>코스 발견</strong><span>내게 맞는 강원 코스를 찾아요</span></div>
          <div><b>02</b><strong>러닝 시작</strong><span>AI 날씨·안전 브리핑과 함께 달려요</span></div>
          <div><b>03</b><strong>완주 인증</strong><span>나만의 발자국을 남겨요</span></div>
        </div>
      </section>
    </main>
  );
}
