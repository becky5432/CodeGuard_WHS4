import { useState } from "react";
import { MetricIcon } from "../components/Icons";
import EXECUTION_RESULT_PRESENTATION from "../components/executionPresentation";

const HISTORY_RESULT_PRESENTATION = {
  SUCCESS: {
    state: "success",
    label: "정상 종료",
  },
  ...EXECUTION_RESULT_PRESENTATION,
};

const HISTORY_SUMMARY_ITEMS = [
  { key: "TOTAL", icon: "total", label: "전체 실행 결과" },

  { key: "SUCCESS", icon: "success" },
  { key: "COMPILE_ERROR", icon: "compile" },
  { key: "RUNTIME_ERROR", icon: "runtime" },
  { key: "TIME_LIMIT", icon: "time" },
  { key: "MEMORY_LIMIT", icon: "memory" },
  { key: "CPU_TIME_LIMIT", icon: "cputime" },
  { key: "OUTPUT_LIMIT", icon: "output" },
  { key: "FILESYSTEM_LIMIT", icon: "file" },
  { key: "NETWORK_BLOCKED", icon: "network" },
  { key: "PIDS_LIMIT", icon: "process" },
  { key: "INTERNAL_ERROR", icon: "internal" },
];

const LIMIT_LABELS = {
  TIME_LIMIT: "시간 제한",
  MEMORY_LIMIT: "메모리",
  PIDS_LIMIT: "프로세스",
  OUTPUT_LIMIT: "출력 제한",
  CPU_TIME_LIMIT: "CPU 시간",
  FILESYSTEM_LIMIT: "파일 접근",
  NETWORK_BLOCKED: "네트워크",
};

function getLimitLabel(reasonCode) {
  return LIMIT_LABELS[reasonCode] ?? "-";
}

function getHistoryPresentation(job) {
  if (job.status === "SUCCESS") {
    return HISTORY_RESULT_PRESENTATION.SUCCESS;
  }

  return (
    EXECUTION_RESULT_PRESENTATION[job.reason_code] ?? {
      state: "error",
      label: "실행 실패",
    }
  );
}

function HistoryPage() {
  const [history] = useState([]);
  const [summary] = useState({});
  const [isLoading] = useState(false);
  const [error] = useState(null);

  return (
    <div className="history-page">
      <section className="history-summary-grid">
        {HISTORY_SUMMARY_ITEMS.map(({ key, icon, label }) => {
          const isTotal = key === "TOTAL";

          const presentation = isTotal
            ? {
                state: "total",
                label,
              }
            : HISTORY_RESULT_PRESENTATION[key];

          return (
            <article
              key={key}
              className={`history-summary-card history-summary-${presentation.state}`}
            >
              <span
                className={`history-summary-icon history-summary-icon-${icon}`}
              >
                <MetricIcon type={icon} />
              </span>

              <div>
                <span>{presentation.label}</span>
                <strong>
                  {isTotal
                    ? Object.values(summary).reduce(
                        (total, count) => total + (count ?? 0),
                        0,
                      )
                    : (summary[key] ?? 0)}
                </strong>{" "}
              </div>
            </article>
          );
        })}
      </section>

      <section className="history-list-section">
        <div className="history-list-header">
          <h2>실행 기록 목록</h2>

          <div className="history-toolbar">
            <div className="history-search">
              <input type="search" placeholder="job_id로 검색하세요..." />
            </div>

            <select defaultValue="">
              <option value="">실행 결과</option>
              <option value="SUCCESS">정상 종료</option>
              <option value="ERROR">실패</option>
              <option value="BLOCKED">차단</option>
            </select>

            <select defaultValue="">
              <option value="">제한 항목</option>
              <option value="TIME_LIMIT">시간 제한</option>
              <option value="MEMORY_LIMIT">메모리 제한</option>
              <option value="PIDS_LIMIT">프로세스 제한</option>
              <option value="CPU_TIME_LIMIT">CPU 시간 제한</option>
              <option value="OUTPUT_LIMIT">출력 제한</option>
              <option value="FILESYSTEM_LIMIT">파일 접근 제한</option>
              <option value="NETWORK_BLOCKED">네트워크 접근 제한</option>
            </select>

            <select defaultValue="">
              <option value="">기간 선택</option>
            </select>
          </div>
        </div>

        <div className="history-table-wrap">
          <table className="history-table">
            <thead>
              <tr>
                <th>job_id</th>
                <th>실행 결과</th>
                <th>종료 사유</th>
                <th>제한 항목</th>
                <th>실행 시간</th>
              </tr>
            </thead>

            <tbody>
              {isLoading ? (
                <tr>
                  <td colSpan="5" className="history-empty">
                    실행 기록을 불러오는 중입니다.
                  </td>
                </tr>
              ) : error ? (
                <tr>
                  <td colSpan="5" className="history-empty">
                    실행 기록을 불러오지 못했습니다.
                  </td>
                </tr>
              ) : history.length === 0 ? (
                <tr>
                  <td colSpan="5" className="history-empty">
                    실행 기록이 없습니다.
                  </td>
                </tr>
              ) : (
                history.map((job) => {
                  const presentation = getHistoryPresentation(job);

                  return (
                    <tr key={job.job_id}>
                      <td>
                        <button type="button" className="history-job-link">
                          {job.job_id}
                        </button>
                      </td>

                      <td>
                        <span
                          className={`history-result history-result-${presentation.state}`}
                        >
                          {presentation.state === "success"
                            ? "정상 종료"
                            : presentation.state === "blocked"
                              ? "차단"
                              : "실패"}
                        </span>
                      </td>

                      <td>
                        <strong className="history-reason">
                          {presentation.label}
                        </strong>

                        {job.error_message && (
                          <span className="history-reason-detail">
                            {job.error_message}
                          </span>
                        )}
                      </td>

                      <td>{getLimitLabel(job.reason_code)}</td>

                      <td>{job.started_at ?? job.created_at ?? "-"}</td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

export default HistoryPage;
