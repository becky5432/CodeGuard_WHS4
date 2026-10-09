import { ResultMessageIcon, StatusGlyph } from "../Icons";
import { getExecutionResultPresentation } from "../executionPresentation";
const DISPLAYED_EXECUTION_STAGES = [
  {
    key: "COMPILE",
    label: "컴파일",
  },
  {
    key: "EXECUTE",
    label: "실행",
  },
];

function getExecutionStageStatus(stage, stageSummary) {
  if (!stageSummary) {
    return "waiting";
  }

  if (stageSummary.succeeded?.includes(stage)) {
    return "success";
  }

  if (stageSummary.failed?.includes(stage)) {
    return "failed";
  }

  if (stageSummary.skipped?.includes(stage)) {
    return "skipped";
  }

  return "waiting";
}

function getExecutionStageLabel(status) {
  const labels = {
    waiting: "- 대기",
    success: "✓ 성공",
    failed: "× 실패",
    skipped: "— 건너뜀",
  };

  return labels[status];
}

const POLICY_VIOLATION_MESSAGES = {
  FILESYSTEM_LIMIT: "파일 접근이 차단되었습니다.",
  NETWORK_BLOCKED: "네트워크 접근이 차단되었습니다.",
};

function ExecutionResultPanel({
  executionResult,
  executionState,
  executionStatusText,
  message,
  requestErrorCode = null,
}) {
  const derivedPresentation = getExecutionResultPresentation(executionResult);

  const displayState = executionState ?? derivedPresentation.state;
  const displayStatusText = executionStatusText ?? derivedPresentation.label;
  const displayMessage = message ?? derivedPresentation.message;

  const policyViolations = executionResult?.policy_violations ?? [];

  const executionReasonCode =
    executionResult?.reason_code ?? requestErrorCode ?? "-";

  const executionExitCode = executionResult?.exit_code ?? "-";

  const executionStages = DISPLAYED_EXECUTION_STAGES.map((stage) => {
    const status = getExecutionStageStatus(
      stage.key,
      executionResult?.stage_summary,
    );

    return {
      ...stage,
      status,
      statusLabel: getExecutionStageLabel(status),
    };
  });
  return (
    <section className="workspace-panel result-section">
      <div className="workspace-panel-header">
        <h2>실행 결과</h2>
      </div>

      <div className="execution-result-content">
        {policyViolations.length > 0 && (
          <div className="policy-violation-messages">
            {policyViolations.map((violation) => {
              const violationMessage = POLICY_VIOLATION_MESSAGES[violation];

              if (!violationMessage) {
                return null;
              }

              return (
                <p
                  className="execution-message execution-message-blocked policy-violation-message"
                  key={violation}
                >
                  <span>
                    <ResultMessageIcon status="blocked" />
                  </span>
                  {violationMessage}
                </p>
              );
            })}
          </div>
        )}

        {displayState !== "idle" && displayMessage && (
          <p className={`execution-message execution-message-${displayState}`}>
            <span>
              <ResultMessageIcon status={displayState} />
            </span>

            {displayMessage}
          </p>
        )}

        <div className="execution-result-summary">
          <strong
            className={`execution-status-badge execution-status-${displayState}`}
          >
            {displayStatusText}
          </strong>

          <div className="execution-summary-item">
            <span>종료 코드</span>
            <strong>{executionExitCode}</strong>
          </div>

          <div className="execution-summary-item">
            <span>종료 사유</span>
            <strong>{executionReasonCode}</strong>
          </div>

          <div className="execution-stage-label">단계</div>

          <div className="execution-stage-compact" aria-label="단계별 결과">
            {executionStages.map((stage, index) => (
              <div className="execution-stage-compact-item" key={stage.key}>
                <span className={`stage-dot stage-${stage.status}`}>
                  <StatusGlyph status={stage.status} />
                </span>

                <strong>{stage.label}</strong>

                <span>{stage.statusLabel.replace(/^[^ ]+ /, "")}</span>

                {index < executionStages.length - 1 && (
                  <span className="stage-compact-arrow">→</span>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

export default ExecutionResultPanel;
