import ExecutionResultPanel from "./ExecutionResultPanel";
import OutputPanel from "./OutputPanel";
import ResourceUsagePanel from "./ResourceUsagePanel";
import { MetricIcon } from "../Icons";

const POLICY_FIELDS = [
  {
    key: "timeout_ms",
    label: "실행 시간",
    unit: "ms",
    type: "time",
  },
  {
    key: "memory_limit_mb",
    label: "메모리 제한",
    unit: "MB",
    type: "memory",
  },
  {
    key: "pids_limit",
    label: "PID 제한",
    unit: "개",
    type: "process",
  },
  {
    key: "cpu_bandwidth",
    label: "CPU 처리량",
    unit: "CPU",
    type: "cpu",
  },
  {
    key: "cpu_time_limit_ms",
    label: "CPU 시간",
    unit: "ms",
    type: "cputime",
  },
  {
    key: "output_limit_bytes",
    label: "출력 제한",
    unit: "bytes",
    type: "output",
  },
];

function ExecutionDetailPanel({ execution, onClose }) {
  if (!execution) {
    return null;
  }

  return (
    <aside className="history-detail-panel">
      <div className="history-detail-header">
        <div>
          <span>실행 상세</span>
          <strong>{execution.job_id}</strong>
        </div>

        <button
          type="button"
          className="history-detail-close"
          onClick={onClose}
          aria-label="실행 상세 닫기"
        >
          ×
        </button>
      </div>

      <div className="history-detail-content">
        {/* 코드 / 입출력 */}
        <section className="workspace-panel history-detail-section">
          <div className="workspace-panel-header">
            <h2>코드 및 입출력</h2>

            <div className="history-detail-code-meta">
              <span>
                {execution.language === "CPP"
                  ? "C++"
                  : (execution.language ?? "-")}
              </span>

              <span>·</span>

              <span>
                {execution.created_at
                  ? new Date(execution.created_at).toLocaleString("ko-KR")
                  : "-"}
              </span>
            </div>
          </div>
          <div className="history-detail-code-block">
            <span className="history-detail-label">실행 코드</span>
            <pre>{execution.code ?? ""}</pre>
          </div>
          <div className="history-detail-stdin">
            <span className="history-detail-label">표준 입력 (stdin)</span>
            <pre>{execution.stdin || "입력값이 없습니다."}</pre>
          </div>
          <div className="history-detail-output">
            <OutputPanel executionResult={execution} />
          </div>
        </section>

        {/* 당시 자원 제한 설정 */}
        <section className="workspace-panel settings-section history-detail-section">
          <div className="workspace-panel-header">
            <h2>자원 제한 설정</h2>
          </div>

          <div className="settings-content">
            <div className="environment-limit-grid-top policy-setting-grid">
              {POLICY_FIELDS.map((field) => (
                <div className="environment-limit-card" key={field.key}>
                  <span
                    className={`environment-limit-icon metric-${field.type}`}
                  >
                    <MetricIcon type={field.type} />
                  </span>

                  <div>
                    <span>{field.label}</span>

                    <strong>
                      {execution.policy?.[field.key] == null
                        ? "-"
                        : field.key === "timeout_ms" ||
                            field.key === "cpu_time_limit_ms"
                          ? Number(
                              (execution.policy[field.key] / 1000).toFixed(3),
                            )
                          : execution.policy[field.key]}
                      {execution.policy?.[field.key] != null && (
                        <span className="history-detail-policy-unit">
                          {field.unit}
                        </span>
                      )}
                    </strong>
                  </div>
                </div>
              ))}
            </div>

            <div className="fixed-control-section">
              <h3>접근 통제</h3>

              <div className="environment-limit-grid-bottom history-detail-fixed-grid">
                <article className="planned-feature-item">
                  <span className="environment-limit-icon planned-file-icon">
                    <MetricIcon type="file" />
                  </span>

                  <div className="fixed-control-info">
                    <strong>파일 접근 제한</strong>
                    <small className="limit-status-badge">제한 중</small>
                  </div>
                </article>

                <article className="planned-feature-item">
                  <span className="environment-limit-icon planned-network-icon">
                    <MetricIcon type="network" />
                  </span>

                  <div className="fixed-control-info">
                    <strong>네트워크 차단</strong>
                    <small className="limit-status-badge">제한 중</small>
                  </div>
                </article>

                <article className="planned-feature-item">
                  <span className="environment-limit-icon planned-permission-icon">
                    <MetricIcon type="permission" />
                  </span>

                  <div className="fixed-control-info">
                    <strong>권한 제한</strong>
                    <small className="limit-status-badge">제한 중</small>
                  </div>
                </article>
              </div>
            </div>
          </div>
        </section>

        {/* 실행 결과 */}
        <ExecutionResultPanel executionResult={execution} />

        {/* 자원 사용량 */}
        <ResourceUsagePanel
          executionResult={execution}
          policy={execution.policy}
        />
      </div>
    </aside>
  );
}

export default ExecutionDetailPanel;
