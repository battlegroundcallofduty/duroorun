// 관리자 목록(코스/편의시설) 공용 숫자 페이지네이션 - UI 렌더링 파일
// ㅡ 카드가 아닌 목록형이라 더보기 대신 페이지 번호가 적합하다고 판단
const AdminPagination = ({ page, total, size, onPageChange, disabled }) => {
  const totalPages = Math.max(Math.ceil(total / size), 1);
  if (totalPages <= 1) return null;

  // 현재 페이지 기준 앞뒤 2개씩만 보여주고 나머지는 생략(...) 표시
  const pageNumbers = [];
  for (let p = 1; p <= totalPages; p += 1) {
    if (p === 1 || p === totalPages || Math.abs(p - page) <= 2) {
      pageNumbers.push(p);
    } else if (pageNumbers[pageNumbers.length - 1] !== '...') {
      pageNumbers.push('...');
    }
  }

  return (
    <nav className="admin-pagination" aria-label="페이지 이동">
      <button type="button" onClick={() => onPageChange(page - 1)} disabled={disabled || page <= 1}>
        이전
      </button>
      {pageNumbers.map((p, idx) =>
        p === '...' ? (
          <span key={`ellipsis-${idx}`} className="admin-pagination-ellipsis">
            …
          </span>
        ) : (
          <button
            type="button"
            key={p}
            className={p === page ? 'active' : ''}
            onClick={() => onPageChange(p)}
            disabled={disabled || p === page}
          >
            {p}
          </button>
        )
      )}
      <button
        type="button"
        onClick={() => onPageChange(page + 1)}
        disabled={disabled || page >= totalPages}
      >
        다음
      </button>
    </nav>
  );
};

export default AdminPagination;
