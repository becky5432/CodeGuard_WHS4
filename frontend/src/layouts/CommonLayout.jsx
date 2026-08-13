import { Outlet } from "react-router-dom";
import Header from "../components/Header";
import Sidebar from "../components/Sidebar";

function CommonLayout({ title, description }) {
  return (
    <div className="app">
      <Sidebar />

      <div className="page">
        <Header title={title} description={description} />

        <main className="main-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export default CommonLayout;
