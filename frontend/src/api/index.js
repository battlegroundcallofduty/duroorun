const REFRESH_ENDPOINT = '/v1/auth/refresh';

// 이전 버전에서 localStorage에 영구 저장했던 토큰 정리 (새 코드는 더 이상 안 씀)
localStorage.removeItem('accessToken');

// sessionStorage 사용 - 영구 저장 대신 세션 단위 저장으로 변경.
// 탭 하나를 닫아도 다른 탭이 refresh_token 쿠키(탭 간 공유)로 로그인을 복구할 수 있어
// "탭 닫기 = 로그아웃"은 아님. 브라우저 전체 종료 시 사라지는 게 일반적이지만,
// 브라우저의 "이전 세션 복원" 설정에 따라 재실행 후에도 남아있을 수 있음(MDN).
const getAccessToken = () => sessionStorage.getItem('accessToken');
const setAccessToken = (token) => sessionStorage.setItem('accessToken', token);
const clearAccessToken = () => sessionStorage.removeItem('accessToken');

const buildRequest = (options, accessToken) => ({
  ...options,
  credentials: 'include',
  headers: {
    // FormData면 브라우저가 boundary 포함한 Content-Type을 직접 설정해야 하므로 건너뜀
    ...(!(options.body instanceof FormData) && { 'Content-Type': 'application/json' }),
    ...(accessToken && { Authorization: `Bearer ${accessToken}` }),
    ...options.headers,
  },
});

// 401이 동시에 여러 개 발생해도 실제 재발급 요청은 하나만 나가도록 진행 중인 Promise를 공유합니다.
let refreshPromise = null;

const doRefresh = async () => {
  const res = await fetch(`/api${REFRESH_ENDPOINT}`, {
    method: 'POST',
    credentials: 'include',
  });
  if (!res.ok) {
    clearAccessToken();
    return null;
  }
  const data = await res.json();
  setAccessToken(data.access_token);
  return data.access_token;
};

export const refreshAccessToken = () => {
  if (!refreshPromise) {
    refreshPromise = doRefresh().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
};

// 401 응답 시 Refresh Token으로 재발급 후 원요청을 한 번만 재시도합니다.
export const apiFetch = async (url, options = {}) => {
  let accessToken = getAccessToken();
  let res = await fetch(`/api${url}`, buildRequest(options, accessToken));

  if (res.status === 401 && url !== REFRESH_ENDPOINT) {
    accessToken = await refreshAccessToken();
    if (accessToken) {
      res = await fetch(`/api${url}`, buildRequest(options, accessToken));
    }
  }

  return res;
};

export { getAccessToken, setAccessToken, clearAccessToken };
