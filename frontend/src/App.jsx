import { Navigate, Route, Routes } from "react-router-dom";
import "./App.css";
import CommonLayout from "./layouts/CommonLayout";
import MainPage from "./pages/MainPage";
import ResultPage from "./pages/ResultPage";
import HistoryPage from "./pages/HistoryPage";

function App() {
  return (
    <Routes>
      <Route
        element={
          <CommonLayout
            title="코드 입력 및 실행"
            description="C/C++ 코드를 안전한 환경에서 실행합니다."
          />
        }
      >
        <Route path="/" element={<MainPage />} />
      </Route>
      <Route
        element={
          <CommonLayout
            title="실행 결과"
            description="제출한 코드의 컴파일 및 실행 결과를 확인합니다."
          />
        }
      >
        <Route path="/result" element={<ResultPage />} />
      </Route>
      <Route
        element={
          <CommonLayout
            title="실행 기록"
            description="이전에 요청한 코드 실행 내역을 확인합니다."
          />
        }
      >
        <Route path="/history" element={<HistoryPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;
