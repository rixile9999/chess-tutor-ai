import { NavLink, Outlet, useLocation } from 'react-router-dom';
import './shell.css';

const NAV = [
  { to: '/games', label: '기보', icon: IconGames },
  { to: '/review', label: '리뷰', icon: IconReview },
  { to: '/openings', label: '오프닝', icon: IconOpenings },
  { to: '/profile', label: '프로필', icon: IconProfile },
  { to: '/training', label: '훈련', icon: IconTrain },
];

export function Shell() {
  const { pathname } = useLocation();
  return (
    <div className="shell">
      <header className="topbar">
        <div className="wordmark">체스 튜터</div>
        <div className="divider" />
        <div className="topbar-meta" id="topbar-meta" />
        <div className="spacer" />
        <NavLink to="/games?import=1" className="btn btn-ghost">기보 가져오기</NavLink>
      </header>
      <div className="body">
        <nav className="rail">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={({ isActive }) => `rail-item${isActive || pathname.startsWith(to) ? ' active' : ''}`}>
              <Icon />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

const S = { width: 22, height: 22, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
function IconGames() { return <svg {...S}><path d="M4 5h16v14H4z M4 10h16M9 5v14" /></svg>; }
function IconReview() { return <svg {...S}><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M3 9h18M3 15h18M9 3v18M15 3v18" /></svg>; }
function IconOpenings() { return <svg {...S}><circle cx="5" cy="12" r="2.5" /><circle cx="19" cy="6" r="2.5" /><circle cx="19" cy="18" r="2.5" /><path d="M7.4 11L16.6 7M7.4 13l9.2 4" /></svg>; }
function IconProfile() { return <svg {...S}><circle cx="12" cy="8" r="4" /><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7" /></svg>; }
function IconTrain() { return <svg {...S}><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="4.5" /><circle cx="12" cy="12" r="1" /></svg>; }
