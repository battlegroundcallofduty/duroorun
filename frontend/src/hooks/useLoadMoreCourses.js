import { useEffect, useRef, useState } from 'react';

import { apiFetch } from '../api';

// 전체코스/나만의코스 목록 전용 더보기 훅.
// ㅡ usePaginatedCourses(공용 훅, 다른 도메인 페이지들도 씀)와 별개로 코스 목록
//   2곳(CourseList/MyCourses)만 이 훅을 쓴다.
//   - 공용 훅 쪽을 바꾸면 다른 도메인 영역들도 건들게 돼서 새로 팜
// ㅡ RecordHistory.jsx(record 도메인)의 lastFetchedPageRef와 같은 방식.
//   "다음 페이지 번호"를 화면에 남은 고유 항목 수(courses.length)로 역산하면 X.
// ㅡ 실제로 요청에 성공한 서버 페이지 번호를 ref에 직접 기록해서,
//   항목 수와 무관하게 다음엔 몇 페이지를 요청해야 하는지 별도로 관리.
export const useLoadMoreCourses = (path, buildQuery, deps) => {
  const [courses, setCourses] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [loadMoreError, setLoadMoreError] = useState('');
  const requestIdRef = useRef(0);
  const loadingMoreRef = useRef(false); // 중복 클릭 방지(state는 리렌더 전까지 안 바뀌어서 ref로 동기 체크)
  const lastFetchedPageRef = useRef(0); // 실제로 요청에 성공한 마지막 서버 페이지 번호

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
      setCourses((prev) => {
        if (!append) return data.items;
        const existingIds = new Set(prev.map((c) => c.course_id));
        return [...prev, ...data.items.filter((c) => !existingIds.has(c.course_id))];
      });
      setTotal(data.total);
      // 이 요청이 실제로 요청한 페이지(page) 번호를 그대로 기록
      // ㅡ 항목 수가 아니라 page 자체를 신뢰.
      lastFetchedPageRef.current = page;
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
    lastFetchedPageRef.current = 0;
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
      const nextPage = lastFetchedPageRef.current + 1;
      await fetchPage(nextPage, { append: true });
    } finally {
      // fetchPage는 내부에서 에러를 전부 처리해 던지지 않지만, 혹시라도 예외가
      // 새어나와도 "더보기" 버튼이 영구히 막히지 않도록 방어
      loadingMoreRef.current = false;
    }
  };

  // 삭제 등으로 서버 쪽 정렬/개수가 바뀔 수 있는 액션 후 쓰는 전체 재조회.
  // 실제로 요청에 성공한 페이지 수만큼을
  // 원래 페이지 크기로 나눠 병렬로 다시 받아와 통째로 교체
  // (한 번에 courses.length만큼 요청하면 백엔드 size 상한 100에 걸림).
  const reload = async () => {
    if (!path) return false;
    const myRequestId = ++requestIdRef.current;
    loadingMoreRef.current = false;
    setLoadingMore(false);
    setLoadMoreError('');
    const pageCount = Math.max(lastFetchedPageRef.current, 1);
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
      // "서버에 페이지 N개를 요청했다"는 사실은 변하지 않고
      // 중복 제거해서 개수가 줄어들 수 있는 dedup 결과와 무관하게,
      // 다음 더보기는 그 다음 페이지부터 시작해야 한다
      lastFetchedPageRef.current = pageCount;
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
