import { useCallback, useState } from 'react';

import { apiFetch } from '../../api';
import { useAdminPagedList } from '../../hooks/useAdminPagedList';
import AdminPagination from './AdminPagination';

const PAGE_SIZE = 20;
const COURSE_TYPE_LABEL = { DRNB: '두루누비', CUSTOM: '커스텀' };

// 관리자 - 코스 조회 + 활성화/비활성화 전용 (수정 폼 X)
const CourseManagement = () => {
  const [courseType, setCourseType] = useState('');
  const [keywordInput, setKeywordInput] = useState('');
  const [keyword, setKeyword] = useState('');
  const [activeFilter, setActiveFilter] = useState(''); // '':전체/'true':활성/'false':비활성
  const [togglingId, setTogglingId] = useState(null);
  const [toggleError, setToggleError] = useState('');

  const buildQuery = useCallback(
    (targetPage, size = PAGE_SIZE) => {
      const params = new URLSearchParams({ page: targetPage, size });
      if (courseType) params.set('course_type', courseType);
      if (keyword) params.set('keyword', keyword);
      if (activeFilter) params.set('is_active', activeFilter);
      return params.toString();
    },
    [courseType, keyword, activeFilter]
  );

  const {
    items: courses,
    total,
    page,
    loading,
    error,
    goToPage,
    reload,
  } = useAdminPagedList('/v1/courses/admin', buildQuery, [courseType, keyword, activeFilter]);

  const handleSearchSubmit = (event) => {
    event.preventDefault();
    setKeyword(keywordInput.trim());
  };

  const handleToggleActive = async (course) => {
    const nextActive = !course.is_active;
    if (!window.confirm(nextActive ? '이 코스를 다시 활성화할까요?' : '이 코스를 비활성화할까요?')) {
      return;
    }
    setToggleError('');
    setTogglingId(course.course_id);
    try {
      const res = await apiFetch(`/v1/courses/admin/${course.course_id}`, {
        method: 'PATCH',
        body: JSON.stringify({ is_active: nextActive }),
      });
      if (!res.ok) {
        setToggleError('코스 상태 변경에 실패했어요.');
        return;
      }
      const reloaded = await reload();
      if (!reloaded) {
        setToggleError('변경은 됐지만 목록을 새로고침하지 못했어요. 새로고침 해주세요.');
      }
    } catch (err) {
      console.error('코스 상태 변경 실패:', err);
      setToggleError('서버에 연결할 수 없어요. 잠시 후 다시 시도해주세요.');
    } finally {
      setTogglingId(null);
    }
  };

  return (
    <section className="admin-section">
      <h2>코스 관리 ({total}건)</h2>
      <p className="admin-section-hint">코스 조회와 활성화/비활성화 상태를 관리해요.</p>

      <form className="admin-search-form" onSubmit={handleSearchSubmit}>
        <select value={courseType} onChange={(event) => setCourseType(event.target.value)}>
          <option value="">전체 코스</option>
          <option value="DRNB">두루누비</option>
          <option value="CUSTOM">커스텀</option>
        </select>
        <select value={activeFilter} onChange={(event) => setActiveFilter(event.target.value)}>
          <option value="">상태 전체</option>
          <option value="true">활성만</option>
          <option value="false">비활성만</option>
        </select>
        <input
          type="text"
          value={keywordInput}
          onChange={(event) => setKeywordInput(event.target.value)}
          placeholder="코스명 검색"
        />
        <button type="submit" className="primary-button">검색</button>
      </form>

      {loading && <p className="course-list-status">불러오는 중...</p>}
      {error && <p className="course-list-status error">{error}</p>}
      {toggleError && <p className="course-list-status error">{toggleError}</p>}

      {!loading && !error && courses.length === 0 && (
        <p className="course-list-status">조건에 맞는 코스가 없어요.</p>
      )}

      {!loading && !error && courses.length > 0 && (
        <ul className="admin-banned-list">
          {courses.map((course) => (
            <li key={course.course_id} className="admin-banned-item">
              <div>
                <strong>{course.course_name}</strong>
                <span>{COURSE_TYPE_LABEL[course.course_type] ?? course.course_type}</span>
                {course.sigun && <span>{course.sigun}</span>}
                <span className={course.is_active ? 'admin-badge-active' : 'admin-badge-inactive'}>
                  {course.is_active ? '활성' : '비활성'}
                </span>
                {course.is_admin_managed && (
                  <span title="관리자가 직접 활성/비활성화한 코스 - 두루누비 재동기화가 덮어쓰지 않아요">
                    🔒 관리자 지정
                  </span>
                )}
              </div>
              <button
                type="button"
                className="admin-unban-button"
                onClick={() => handleToggleActive(course)}
                disabled={togglingId === course.course_id}
              >
                {togglingId === course.course_id
                  ? '처리 중...'
                  : course.is_active
                    ? '비활성화'
                    : '활성화'}
              </button>
            </li>
          ))}
        </ul>
      )}

      {!loading && !error && (
        <AdminPagination page={page} total={total} size={PAGE_SIZE} onPageChange={goToPage} disabled={loading} />
      )}
    </section>
  );
};

export default CourseManagement;
