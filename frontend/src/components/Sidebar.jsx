import { NavLink } from "react-router-dom";

function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="service-logo">
        <div className="logo-symbol">C</div>

        <div>
          <strong>CodeGuard</strong>
          <span>Linux Sandbox for C/C++</span>
        </div>
      </div>

      <nav className="navigation" aria-label="주요 메뉴">
        <NavLink
          className={({ isActive }) =>
            `navigation-item${isActive ? " active" : ""}`
          }
          to="/"
          end
        >
          코드 입력 및 실행
        </NavLink>

        <NavLink
          className={({ isActive }) =>
            `navigation-item${isActive ? " active" : ""}`
          }
          to="/history"
        >
          실행 기록
        </NavLink>

        <button className="navigation-item" type="button" disabled>
          대시보드
        </button>

        <button className="navigation-item" type="button" disabled>
          리포트
        </button>
      </nav>

      <div className="service-status">
        <strong>서비스 상태</strong>
        <p>
          <span className="status-dot" />
          실행 환경 준비 중
        </p>
      </div>
    </aside>
  );
}

export default Sidebar;
