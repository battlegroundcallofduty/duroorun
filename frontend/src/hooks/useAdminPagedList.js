import { useEffect, useRef, useState } from 'react';

import { apiFetch } from '../api';

// 관리자 목록 화면(코스 관리/편의시설 관리) 전용 - 페이지 번호 이동 훅.
// 한 번에 한 페이지 분량만 들고 있다가 page가 바뀌면 그 페이지 통째로 새로 받아옴
export const useAdminPagedList = (path, buildQuery, deps) => {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const requestIdRef = useRef(0);
  const sizeRef = useRef(20); // 응답 size 필드에서 갱신 - "마지막 페이지가 지금 몇 번인지" 계산
  // reload()는 상태 변경(토글 등) API 요청이 끝난 뒤에 호출(버튼 눌렀을떄 시점 필터 기억)
  // ㅡ 비활성화 버튼 누르고 서버 응답 기다리는 동안 화면 목록과 실제 선택 필터 어긋날 수 있음.
  // page, buildQuery(필터 조건 함수)를 ref에 저장해두고, 렌더될때마다 최신값으로 ref 덮어씀.
  const pageRef = useRef(1);
  const buildQueryRef = useRef(buildQuery);
  pageRef.current = page;
  buildQueryRef.current = buildQuery;

  const fetchPage = async (targetPage, { allowClamp = true } = {}) => {
    if (!path) return false;
    const myRequestId = ++requestIdRef.current;
    setLoading(true);
    setError('');
    try {
      const query = buildQueryRef.current(targetPage);
      const res = await apiFetch(query ? `${path}?${query}` : path);
      if (requestIdRef.current !== myRequestId) return true;
      if (!res.ok) {
        setError('목록을 불러오지 못했어요.');
        return false;
      }
      const data = await res.json();
      if (requestIdRef.current !== myRequestId) return true;
      sizeRef.current = data.size || sizeRef.current;

      // 마지막 페이지 항목 지우거나 비활성화해서 그 페이지가 비어버리면
      // (전체는 남아있는데 targetPage만 범위 밖이 된 경우)
      // "결과 없음" 대신 실제 마지막 페이지로 한 번만 다시 받아옴.
      if (allowClamp && data.items.length === 0 && targetPage > 1 && data.total > 0) {
        const lastPage = Math.max(Math.ceil(data.total / sizeRef.current), 1);
        if (lastPage !== targetPage) {
          return fetchPage(lastPage, { allowClamp: false });
        }
      }

      setItems(data.items);
      setTotal(data.total);
      setPage(targetPage);
      pageRef.current = targetPage;
      return true;
    } catch (err) {
      if (requestIdRef.current === myRequestId) {
        console.error('관리자 목록 조회 실패:', err);
        setError('서버에 연결할 수 없어요. 잠시 후 다시 시도해주세요.');
      }
      return false;
    } finally {
      if (requestIdRef.current === myRequestId) setLoading(false);
    }
  };

  useEffect(() => {
    if (!path) return undefined;
    fetchPage(1);
    return () => {
      requestIdRef.current += 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);

  const goToPage = (targetPage) => fetchPage(targetPage);
  // 수정/토글 등 액션 후 지금 보고 있는 페이지 그대로 새로고침
  // ㅡ page(클로저 변수) 대신 pageRef.current를 읽어,
  // 이 reload가 호출되는 시점 기준 최신 페이지를 쓴다
  const reload = () => fetchPage(pageRef.current);

  return { items, total, page, loading, error, goToPage, reload };
};
