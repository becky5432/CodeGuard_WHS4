import ExecutionResultPanel from "./ExecutionResultPanel";
import OutputPanel from "./OutputPanel";
import ResourceUsagePanel from "./ResourceUsagePanel";

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
        <ExecutionResultPanel executionResult={execution} />

        <ResourceUsagePanel
          executionResult={execution}
          policy={execution.policy}
        />

        <OutputPanel executionResult={execution} />
      </div>
    </aside>
  );
}

export default ExecutionDetailPanel;
