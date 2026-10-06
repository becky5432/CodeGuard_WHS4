import { useEffect, useState } from "react";
import { MetricIcon } from "../components/Icons";
import EXECUTION_RESULT_PRESENTATION from "../components/executionPresentation";
import ExecutionDetailPanel from "../components/execution/ExecutionDetailPanel";
import { getExecution, getExecutions } from "../api/executionApi";

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

function formatDateTime(value) {
  if (!value) {
    return "-";
  }

  return new Date(value).toLocaleString("ko-KR");
}

function getHistoryPresentation(job) {
  if (job.status === "SUCCESS") {
    return HISTORY_RESULT_PRESENTATION.SUCCESS;
  }

  if (job.status === "PENDING") {
    return {
      state: "waiting",
      label: "대기 중",
    };
  }

  if (job.status === "RUNNING") {
    return {
      state: "loading",
      label: "실행 중",
    };
  }

  return (
    EXECUTION_RESULT_PRESENTATION[job.reason_code] ?? {
      state: "error",
      label: "실행 실패",
    }
  );
}

function HistoryPage() {
  const [history, setHistory] = useState([]);
  const [summary, setSummary] = useState({});
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedExecution, setSelectedExecution] = useState(null);

  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [reasonFilter, setReasonFilter] = useState("");
  const [periodFilter, setPeriodFilter] = useState("");
  const [summaryFilter, setSummaryFilter] = useState("TOTAL");

  const filteredHistory = history.filter((job) => {
    if (
      searchQuery &&
      !job.job_id.toLowerCase().includes(searchQuery.toLowerCase())
    ) {
      return false;
    }

    // 실행 결과 필터
    if (statusFilter && job.status !== statusFilter) {
      return false;
    }

    // 제한 항목 필터
    if (reasonFilter && job.reason_code !== reasonFilter) {
      return false;
    }

    // summary 카드 필터
    if (summaryFilter !== "TOTAL") {
      if (summaryFilter === "SUCCESS") {
        if (job.status !== "SUCCESS") {
          return false;
        }
      } else if (summaryFilter === "COMPILE_ERROR") {
        if (
          job.reason_code !== "COMPILE_ERROR" &&
          job.reason_code !== "COMPILE_TIMEOUT"
        ) {
          return false;
        }
      } else if (job.reason_code !== summaryFilter) {
        return false;
      }
    }

    // 기간 필터
    if (periodFilter) {
      const createdAt = new Date(job.created_at);
      const now = new Date();

      const days =
        periodFilter === "7D" ? 7 : periodFilter === "30D" ? 30 : null;

      if (days) {
        const startDate = new Date(now);
        startDate.setDate(now.getDate() - days);

        if (createdAt < startDate) {
          return false;
        }
      }
    }

    return true;
  });

  const handleResetFilters = () => {
    setSearchQuery("");
    setStatusFilter("");
    setReasonFilter("");
    setPeriodFilter("");
    setSummaryFilter("TOTAL");
  };

  useEffect(() => {
    const loadHistory = async () => {
      try {
        setIsLoading(true);
        setError(null);

        const executions = await getExecutions({
          limit: 100,
          offset: 0,
        });

        setHistory(executions);

        const counts = executions.reduce((acc, job) => {
          if (job.status === "SUCCESS") {
            acc.SUCCESS = (acc.SUCCESS ?? 0) + 1;
            return acc;
          }

          let key = job.reason_code;

          // 컴파일 시간 초과는 컴파일 오류 카드에 포함
          if (key === "COMPILE_TIMEOUT") {
            key = "COMPILE_ERROR";
          }

          if (key) {
            acc[key] = (acc[key] ?? 0) + 1;
          }

          return acc;
        }, {});

        setSummary(counts);
      } catch (err) {
        setError(err);
      } finally {
        setIsLoading(false);
      }
    };

    loadHistory();
  }, []);

  const handleSelectJob = async (jobId) => {
    try {
      const execution = await getExecution(jobId);
      setSelectedExecution(execution);
    } catch (err) {
      console.error("실행 상세 조회 실패:", err);
    }
  };

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
            <button
              key={key}
              type="button"
              className={`history-summary-card history-summary-${presentation.state}${
                summaryFilter === key ? " selected" : ""
              }`}
              onClick={() =>
                setSummaryFilter((current) =>
                  current === key && key !== "TOTAL" ? "TOTAL" : key,
                )
              }
            >
              <span
                className={`history-summary-icon history-summary-icon-${icon}`}
              >
                <MetricIcon type={icon} />
              </span>

              <div>
                <span>{presentation.label}</span>
                <strong>
                  {isTotal ? history.length : (summary[key] ?? 0)}
                </strong>
              </div>
            </button>
          );
        })}
      </section>
      <div
        className={`history-layout${
          selectedExecution ? " history-layout-detail-open" : ""
        }`}
      >
        <section className="history-list-section">
          <div className="history-list-header">
            <h2>실행 기록 목록</h2>

            <div className="history-toolbar">
              <div className="history-search">
                <input
                  type="search"
                  placeholder="job_id로 검색하세요..."
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                />
              </div>

              <select
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value)}
              >
                <option value="">실행 결과</option>
                <option value="SUCCESS">정상 종료</option>
                <option value="ERROR">실패</option>
                <option value="BLOCKED">차단</option>
              </select>

              <select
                value={reasonFilter}
                onChange={(event) => setReasonFilter(event.target.value)}
              >
                <option value="">제한 항목</option>
                <option value="TIME_LIMIT">시간 제한</option>
                <option value="MEMORY_LIMIT">메모리 제한</option>
                <option value="PIDS_LIMIT">프로세스 제한</option>
                <option value="CPU_TIME_LIMIT">CPU 시간 제한</option>
                <option value="OUTPUT_LIMIT">출력 제한</option>
                <option value="FILESYSTEM_LIMIT">파일 접근 제한</option>
                <option value="NETWORK_BLOCKED">네트워크 접근 제한</option>
              </select>

              <select
                value={periodFilter}
                onChange={(event) => setPeriodFilter(event.target.value)}
              >
                <option value="">기간 선택</option>
                <option value="7D">최근 7일</option>
                <option value="30D">최근 30일</option>
              </select>

              <button
                type="button"
                className="history-filter-reset"
                onClick={handleResetFilters}
              >
                초기화
              </button>
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
                  <th>실행 시각</th>
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
                ) : filteredHistory.length === 0 ? (
                  <tr>
                    <td colSpan="5" className="history-empty">
                      조건에 맞는 실행 기록이 없습니다.
                    </td>
                  </tr>
                ) : (
                  filteredHistory.map((job) => {
                    const presentation = getHistoryPresentation(job);

                    return (
                      <tr key={job.job_id}>
                        <td>
                          <button
                            type="button"
                            className="history-job-link"
                            onClick={() => handleSelectJob(job.job_id)}
                          >
                            {job.job_id}
                          </button>
                        </td>
                        <td>
                          <span
                            className={`history-result history-result-${presentation.state}`}
                          >
                            {job.status === "SUCCESS"
                              ? "정상 종료"
                              : job.status === "BLOCKED"
                                ? "차단"
                                : job.status === "PENDING"
                                  ? "대기 중"
                                  : job.status === "RUNNING"
                                    ? "실행 중"
                                    : "실패"}
                          </span>
                        </td>
                        <td>
                          <strong className="history-reason">
                            {presentation.label}
                          </strong>
                        </td>
                        <td>{getLimitLabel(job.reason_code)}</td>
                        <td>{formatDateTime(job.created_at)}</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </section>

        <ExecutionDetailPanel
          execution={selectedExecution}
          onClose={() => setSelectedExecution(null)}
        />
      </div>
    </div>
  );
}

export default HistoryPage;
