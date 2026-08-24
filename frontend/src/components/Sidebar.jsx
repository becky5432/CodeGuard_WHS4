import { NavLink } from "react-router-dom";

function HomeIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 11.5 12 4l9 7.5" />
      <path d="M5.5 10v10h13V10" />
      <path d="M9.5 20v-6h5v6" />
    </svg>
  );
}
/* 중간발표 이후 실행 기록 메뉴 활성화 시 함께 복원
function HistoryIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3.5 12a8.5 8.5 0 1 0 2.4-5.9" />
      <path d="M3.5 5.5v5h5" />
      <path d="M12 7.5V12l3 2" />
    </svg>
  );
}
*/

function ChevronIcon({ isCollapsed }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="22"
      height="22"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {isCollapsed ? (
        <>
          <path d="m8 6 6 6-6 6" />
          <path d="m13 6 6 6-6 6" />
        </>
      ) : (
        <>
          <path d="m16 6-6 6 6 6" />
          <path d="m11 6-6 6 6 6" />
        </>
      )}
    </svg>
  );
}

function Sidebar({ isCollapsed, onToggle }) {
  return (
    <aside className={`sidebar${isCollapsed ? " sidebar-collapsed" : ""}`}>
      <div className="service-logo">
        <div className="logo-symbol" aria-hidden="true">
          C
        </div>

        {!isCollapsed && (
          <div className="service-logo-text">
            <strong>CodeGuard</strong>
            <span>Linux Sandbox for C/C++</span>
          </div>
        )}
      </div>

      <nav className="navigation" aria-label="주요 메뉴">
        <NavLink
          className={({ isActive }) =>
            `navigation-item${isActive ? " active" : ""}`
          }
          to="/"
          end
          title={isCollapsed ? "메인" : undefined}
        >
          <span className="navigation-icon">
            <HomeIcon />
          </span>

          {!isCollapsed && <span className="navigation-label">메인</span>}
        </NavLink>

        {/* 중간발표 이후 실행 기록 화면 구현 시 다시 활성화
        <NavLink
          className={({ isActive }) =>
            `navigation-item${isActive ? " active" : ""}`
          }
          to="/history"
          title={isCollapsed ? "실행 기록" : undefined}
        >
          <span className="navigation-icon">
            <HistoryIcon />
          </span>

          {!isCollapsed && <span className="navigation-label">실행 기록</span>}
        </NavLink>
        */}
      </nav>

      {!isCollapsed ? (
        <div className="service-status">
          <strong>서비스 상태</strong>

          <p>
            <span className="status-dot" />
            실행 환경 준비 중
          </p>
        </div>
      ) : (
        <div
          className="collapsed-service-status"
          title="실행 환경 준비 중"
          aria-label="실행 환경 준비 중"
        >
          <span className="status-dot" />
        </div>
      )}

      <button
        className="sidebar-toggle"
        type="button"
        aria-label={isCollapsed ? "사이드바 펼치기" : "사이드바 접기"}
        title={isCollapsed ? "사이드바 펼치기" : "사이드바 접기"}
        onClick={onToggle}
      >
        <ChevronIcon isCollapsed={isCollapsed} />
      </button>
    </aside>
  );
}

export default Sidebar;
