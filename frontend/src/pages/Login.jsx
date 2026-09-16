import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { apiFetch, setAccessToken } from '../api';
import { useUser } from '../contexts/UserContext';

const PROVIDERS = [
  { key: 'kakao', label: '카카오로 시작하기' },
  { key: 'naver', label: '네이버로 시작하기' },
  { key: 'google', label: '구글로 시작하기' },
];

const ERROR_DISPLAY_MS = 5000;

// 소셜 로그인은 브라우저 전체 리다이렉트라 fetch가 아닌 페이지 이동으로 시작합니다.
const startLogin = (provider) => {
  window.location.href = `/api/v1/auth/${provider}`;
};

const Login = () => {
  const navigate = useNavigate();
  const { refreshUser } = useUser();
  const [error, setError] = useState(() => new URLSearchParams(window.location.search).get('error'));

  // 공모전 심사위원용 임시 기능 - 고정 이메일/비밀번호로 관리자 계정 체험 로그인
  const [showDemoForm, setShowDemoForm] = useState(false);
  const [demoEmail, setDemoEmail] = useState('');
  const [demoPassword, setDemoPassword] = useState('');
  const [demoError, setDemoError] = useState('');
  const [demoLoading, setDemoLoading] = useState(false);

  useEffect(() => {
    if (!error) return undefined;

    // 새로고침 시 에러가 다시 뜨지 않도록 쿼리스트링을 바로 지운다.
    window.history.replaceState(null, '', '/login');

    const timer = setTimeout(() => setError(null), ERROR_DISPLAY_MS);
    return () => clearTimeout(timer);
  }, [error]);

  const handleDemoLogin = async (event) => {
    event.preventDefault();
    setDemoLoading(true);
    setDemoError('');
    try {
      const res = await apiFetch('/v1/auth/demo-admin-login', {
        method: 'POST',
        body: JSON.stringify({ email: demoEmail, password: demoPassword }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        setDemoError(data?.detail ?? '로그인에 실패했어요.');
        return;
      }
      const data = await res.json();
      setAccessToken(data.access_token);
      const { user } = await refreshUser();
      if (!user) {
        setDemoError('로그인 처리 중 오류가 발생했어요.');
        return;
      }
      navigate('/', { replace: true });
    } catch (err) {
      console.error('관리자 체험 로그인 실패:', err);
      setDemoError('서버에 연결할 수 없어요. 잠시 후 다시 시도해주세요.');
    } finally {
      setDemoLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card">
        <p className="login-eyebrow">두루런과 함께</p>
        <h1>바다를 따라, 나답게 달려요</h1>
        <p className="login-desc">소셜 계정으로 간편하게 시작해보세요</p>

        {error && <p className="login-error">{error}</p>}

        <div className="login-buttons">
          {PROVIDERS.map(({ key, label }) => (
            <button
              key={key}
              type="button"
              className={`social-button ${key}`}
              onClick={() => startLogin(key)}
            >
              {label}
            </button>
          ))}
        </div>

        <button
          type="button"
          className="text-button demo-admin-toggle"
          onClick={() => setShowDemoForm((open) => !open)}
        >
          관리자 체험 로그인
        </button>

        {showDemoForm && (
          <form className="demo-admin-form" onSubmit={handleDemoLogin}>
            <input
              type="email"
              value={demoEmail}
              onChange={(event) => setDemoEmail(event.target.value)}
              placeholder="이메일"
              required
            />
            <input
              type="password"
              value={demoPassword}
              onChange={(event) => setDemoPassword(event.target.value)}
              placeholder="비밀번호"
              required
            />
            {demoError && <p className="login-error">{demoError}</p>}
            <button type="submit" className="primary-button" disabled={demoLoading}>
              {demoLoading ? '로그인 중...' : '로그인'}
            </button>
          </form>
        )}
      </div>
    </div>
  );
};

export default Login;
