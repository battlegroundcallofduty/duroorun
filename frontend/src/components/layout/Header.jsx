import { useEffect, useRef, useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';

import { apiFetch, clearAccessToken } from '../../api';
import { useUser } from '../../contexts/UserContext';

// 모바일(<=900px)에서 상단 nav 대신 하단에 고정으로 보여줄 탭.
// 아이콘은 두루런 마스코트 두루미 이미지(배경 투명 처리된 PNG) 사용.
const BOTTOM_TABS = [
  { to: '/courses', end: true, label: '코스 찾기', icon: '/assets/dd1.png' },
  { to: '/records', label: '러닝 기록', icon: '/assets/dd3.png' },
  { to: '/courses/custom/mine', label: '나만의 코스', icon: '/assets/dd2.png' },
];

const Header = () => {
  const navigate = useNavigate();
  const profileMenuRef = useRef(null);
  const { user, setUser } = useUser();
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const handleClickOutside = (event) => {
      if (profileMenuRef.current && !profileMenuRef.current.contains(event.target)) {
        setMenuOpen(false);
      }
    };
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') setMenuOpen(false);
    };
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [menuOpen]);

  const handleLogout = async () => {
    try {
      await apiFetch('/v1/auth/logout', { method: 'POST' });
    } catch {
      // 로그아웃 API 실패해도 클라이언트 쪽 정리는 finally에서 계속 진행 — 콘솔에만 남겨둠
      console.error('로그아웃 요청이 실패했어요');
    } finally {
      clearAccessToken();
      setUser(null);
      setMenuOpen(false);
      navigate('/', { replace: true });
    }
  };

  return (
    <>
      <header className="site-header">
        <a className="brand" href="/" aria-label="두루런 홈">
          <span className="brand-mark">두루</span><span>런</span>
        </a>
        <nav aria-label="주요 메뉴">
          <NavLink to="/courses" end>코스 찾기</NavLink>
          <NavLink to="/records">러닝 기록</NavLink>
          <NavLink to="/courses/custom/mine">나만의 코스</NavLink>
        </nav>
        <div className="header-actions">
          {user ? (
            <div className="profile-menu" ref={profileMenuRef}>
              <div className="profile-avatar" aria-hidden="true">
                <img src={user.profile_image_url || '/assets/default-avatar.png'} alt="" />
              </div>
              <button
                type="button"
                className="profile-nickname"
                onClick={() => setMenuOpen((open) => !open)}
                aria-expanded={menuOpen}
                aria-haspopup="menu"
              >
                {user.nickname || '이름 없음'}
              </button>
              {menuOpen && (
                <div className="profile-dropdown" role="menu">
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setMenuOpen(false);
                      navigate('/mypage');
                    }}
                  >
                    마이페이지
                  </button>
                  {user.user_role === 'ADMIN' && (
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setMenuOpen(false);
                        navigate('/admin');
                      }}
                    >
                      관리자 페이지
                    </button>
                  )}
                  <button type="button" role="menuitem" onClick={handleLogout}>
                    로그아웃
                  </button>
                </div>
              )}
            </div>
          ) : (
            <button className="login-button" onClick={() => navigate('/login')}>로그인</button>
          )}
        </div>
      </header>
      <nav className="bottom-tab-bar" aria-label="주요 메뉴">
        {BOTTOM_TABS.map((tab) => (
          <NavLink key={tab.to} to={tab.to} end={tab.end}>
            <span className="bottom-tab-icon" aria-hidden="true">
              <img src={tab.icon} alt="" />
            </span>
            <span>{tab.label}</span>
          </NavLink>
        ))}
      </nav>
    </>
  );
};

export default Header;
