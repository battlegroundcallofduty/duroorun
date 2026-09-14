import { useEffect, useRef, useState } from 'react';

import { apiFetch } from '../api';

// 전체코스/나만의코스 목록 전용 더보기 훅.
// ㅡ usePaginatedCourses(공용 훅, 다른 도메인 페이지들도 씀)와 별개로 코스 목록
//   2곳(CourseList/MyCourses)만 이 훅을 쓴다.
//   - 공용 훅 쪽을 바꾸면 다른 도메인 영역들도 건들게 돼서 새로 팜
// ㅡ CourseDetail.jsx의 리뷰 더보기(review 도메인)와 같은 방식.
//   따로 들고 다니는 "다음 페이지 번호" 카운터 대신 "지금까지 실제로 불러온
//   개수(courses.length)"에서 매번 다음 페이지를 다시 계산.
export const useLoadMoreCourses = (path, buildQuery, deps) => {
  const [courses, setCourses] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [loadMoreError, setLoadMoreError] = useState('');
  const requestIdRef = useRef(0);
  const loadingMoreRef = useRef(false); // 중복 클릭 방지(state는 리렌더 전까지 안 바뀌어서 ref로 동기 체크)
  const sizeRef = useRef(20); // 응답의 size 필드에서 매번 갱신("다음 페이지 번호" 계산에 사용)

  const fetchPage = async (page, { append }) => {
    const myRequestId = ++requestIdRef.current;
    if (append) setLoadingMore(true);
    else setLoading(true);
    if (append) setLoadMoreError('');
    else setError('');
    try {
      const query = buildQuery(page);
      const res = await apiFetch(query ? `${path}?${query}` : path);
      if (requestIdRef.current !== myRequestId) return true;
      if (!res.ok) {
        if (append) setLoadMoreError('코스 목록을 불러오지 못했어요.');
        else setError('코스 목록을 불러오지 못했어요.');
        return false;
      }
      const data = await res.json();
      if (requestIdRef.current !== myRequestId) return true;
      sizeRef.current = data.size || sizeRef.current;
      setCourses((prev) => {
        if (!append) return data.items;
        const existingIds = new Set(prev.map((c) => c.course_id));
        return [...prev, ...data.items.filter((c) => !existingIds.has(c.course_id))];
      });
      setTotal(data.total);
      return true;
    } catch (err) {
      if (requestIdRef.current === myRequestId) {
        console.error('코스 목록 조회 실패:', err);
        if (append) setLoadMoreError('서버에 연결할 수 없어요. 잠시 후 다시 시도해주세요.');
        else setError('서버에 연결할 수 없어요. 잠시 후 다시 시도해주세요.');
      }
      return false;
    } finally {
      if (requestIdRef.current === myRequestId) {
        if (append) setLoadingMore(false);
        else setLoading(false);
      }
    }
  };

  useEffect(() => {
    // path 아직 없으면(예: user 로딩 전) 대기 - 준비되면 deps 변경으로 재실행
    if (!path) return undefined;
    loadingMoreRef.current = false;
    setLoadingMore(false);
    setLoadMoreError('');
    fetchPage(1, { append: false });
    return () => {
      // 다음 탭/필터 변경으로 새 1페이지 요청이 뜨면, 진행 중이던 더보기 응답은 버림
      requestIdRef.current += 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);

  const loadMore = async () => {
    if (!path || loadingMoreRef.current) return;
    loadingMoreRef.current = true;
    try {
      const nextPage = Math.floor(courses.length / sizeRef.current) + 1;
      await fetchPage(nextPage, { append: true });
    } finally {
      // fetchPage는 내부에서 에러를 전부 처리해 던지지 않지만, 혹시라도 예외가
      // 새어나와도 "더보기" 버튼이 영구히 막히지 않도록 방어
      loadingMoreRef.current = false;
    }
  };

  // 삭제 등으로 서버 쪽 정렬/개수가 바뀔 수 있는 액션 후 쓰는 전체 재조회.
  // 지금까지 불러온 범위(courses.length)만큼을 원래 페이지 크기로 나눠 병렬로 다시 받아와
  // 통째로 교체 (한 번에 courses.length만큼 요청하면 백엔드 size 상한 100에 걸림).
  const reload = async () => {
    if (!path) return false;
    const myRequestId = ++requestIdRef.current;
    loadingMoreRef.current = false;
    setLoadingMore(false);
    setLoadMoreError('');
    const size = sizeRef.current;
    const pageCount = Math.max(Math.ceil(courses.length / size), 1);
    try {
      const responses = await Promise.all(
        Array.from({ length: pageCount }, (_, i) => {
          const query = buildQuery(i + 1);
          return apiFetch(query ? `${path}?${query}` : path);
        })
      );
      if (requestIdRef.current !== myRequestId) return true; // 더 최신 요청이 이미 덮어씀
      if (responses.some((res) => !res.ok)) return false;
      const pages = await Promise.all(responses.map((res) => res.json()));
      if (requestIdRef.current !== myRequestId) return true;
      const seenIds = new Set();
      const merged = [];
      for (const data of pages) {
        for (const item of data.items) {
          if (!seenIds.has(item.course_id)) {
            seenIds.add(item.course_id);
            merged.push(item);
          }
        }
      }
      setCourses(merged);
      setTotal(pages[pages.length - 1].total);
      return true;
    } catch (err) {
      if (requestIdRef.current === myRequestId) {
        console.error('코스 목록 재조회 실패:', err);
      }
      return false;
    }
  };

  return { courses, total, loading, loadingMore, error, loadMoreError, loadMore, reload };
};
