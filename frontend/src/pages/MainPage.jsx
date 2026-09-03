import { useRef, useState } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { cpp } from "@codemirror/lang-cpp";
import { ApiError, createExecution, getExecution } from "../api/executionApi";

const DEFAULT_CODE = `#include <iostream>
using namespace std;

int main() {
    cout << "Hello, CodeGuard!" << endl;
    return 0;
}`;

const DISPLAYED_EXECUTION_STAGES = [
  {
    key: "COMPILE",
    label: "컴파일",
  },
  {
    key: "EXECUTE",
    label: "실행",
  },
  // {
  //   key: "CLEANUP",
  //   label: "정상 종료",
  // },
];

const ACTIVE_POLICY = {
  timeoutMs: 1000,
  memoryLimitMb: 64,
  processLimit: 32,
  cpuLimit: 1.0,
};

const POLLING_INTERVAL_MS = 1000;

const EXECUTION_RESULT_PRESENTATION = {
  COMPILE_ERROR: {
    state: "error",
    label: "컴파일 오류",
    message: "코드를 컴파일하지 못했습니다. 컴파일 로그를 확인해주세요.",
  },
  COMPILE_TIMEOUT: {
    state: "error",
    label: "컴파일 시간 초과",
    message: "컴파일 제한 시간을 초과했습니다.",
  },
  RUNTIME_ERROR: {
    state: "error",
    label: "런타임 오류",
    message: "프로그램 실행 중 오류가 발생했습니다. stderr를 확인해주세요.",
  },
  TIME_LIMIT: {
    state: "blocked",
    label: "시간 제한 초과",
    message: "실행 시간 제한을 초과하여 실행이 중지되었습니다.",
  },
  MEMORY_LIMIT: {
    state: "blocked",
    label: "메모리 제한 초과",
    message: "메모리 제한을 초과하여 실행이 중지되었습니다.",
  },
  PIDS_LIMIT: {
    state: "blocked",
    label: "프로세스 제한 초과",
    message: "프로세스 및 스레드 제한을 초과하여 실행이 중지되었습니다.",
  },
};

const wait = (delay) => new Promise((resolve) => setTimeout(resolve, delay));

function MetricIcon({ type }) {
  const iconPaths = {
    time: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3.8 2.3" />
      </>
    ),
    memory: (
      <>
        <rect x="5" y="6" width="14" height="12" rx="1.5" />
        <path d="M8 9h8M8 12h8M8 15h5M8 3v3M12 3v3M16 3v3M8 18v3M12 18v3M16 18v3" />
      </>
    ),
    process: (
      <>
        <circle cx="8.5" cy="8" r="3.2" />
        <circle cx="16.5" cy="9" r="2.7" />
        <path d="M3.5 19c.5-3 2.2-4.7 5-4.7s4.6 1.7 5.2 4.7M14.2 14.8c2.8-.6 5.4.7 6.2 3.8" />
      </>
    ),
    cpu: (
      <>
        <rect x="6" y="6" width="12" height="12" rx="1.5" />
        <rect x="9.5" y="9.5" width="5" height="5" rx="0.5" />
        <path d="M9 3v3M12 3v3M15 3v3M9 18v3M12 18v3M15 18v3M3 9h3M3 12h3M3 15h3M18 9h3M18 12h3M18 15h3" />
      </>
    ),
    file: (
      <>
        <path d="M6 3.5h7l5 5V20.5H6z" />
        <path d="M13 3.5v5h5M9 13h6M9 16h6" />
      </>
    ),
    network: (
      <>
        <circle cx="12" cy="12" r="8.5" />
        <path d="M3.8 12h16.4M12 3.5c2.1 2.3 3.2 5.1 3.2 8.5s-1.1 6.2-3.2 8.5c-2.1-2.3-3.2-5.1-3.2-8.5S9.9 5.8 12 3.5z" />
      </>
    ),
    permission: (
      <>
        <rect x="5.5" y="10" width="13" height="10" rx="2" />
        <path d="M8.5 10V7.5a3.5 3.5 0 0 1 7 0V10M12 14v2.5" />
      </>
    ),
    output: (
      <>
        <path d="M7 9V4h10v5" />
        <path d="M6 9h12a2 2 0 0 1 2 2v5H4v-5a2 2 0 0 1 2-2Z" />
        <path d="M7 16h10v4H7z" />
        <path d="M16 12h.01" />
      </>
    ),
  };

  return (
    <svg
      className="metric-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.25"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {iconPaths[type]}
    </svg>
  );
}

function StatusGlyph({ status }) {
  if (status === "success") {
    return (
      <svg
        className="status-glyph"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="m5 12 4.2 4.2L19 6.5" />
      </svg>
    );
  }

  if (status === "failed") {
    return (
      <svg
        className="status-glyph"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        aria-hidden="true"
      >
        <path d="m8 8 8 8M16 8l-8 8" />
      </svg>
    );
  }

  return (
    <svg
      className="status-glyph"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="7" />
      <path d="M9.5 12h5" />
    </svg>
  );
}

function ResultMessageIcon({ status }) {
  const isSuccess = status === "success";
  const isFailure = status === "error" || status === "blocked";

  return (
    <svg
      className="alert-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" fill="currentColor" stroke="none" />
      {isSuccess && (
        <path d="m7.2 12.2 3.1 3.1 6.5-6.7" stroke="#fff" strokeWidth="2.4" />
      )}
      {isFailure && (
        <path d="m8.5 8.5 7 7M15.5 8.5l-7 7" stroke="#fff" strokeWidth="2.4" />
      )}
      {!isSuccess && !isFailure && (
        <path d="M12 7.8v5.3M12 16.4h.01" stroke="#fff" strokeWidth="2.4" />
      )}
    </svg>
  );
}

function ArrowIcon() {
  return (
    <svg
      className="stage-arrow-icon"
      viewBox="0 0 42 28"
      fill="none"
      stroke="currentColor"
      strokeWidth="3.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 14h31" />
      <path d="m26 7 7 7-7 7" />
    </svg>
  );
}

function calculateUsagePercentage(value, limit) {
  if (!value || !limit) {
    return 0;
  }

  return Math.min(Math.round((value / limit) * 100), 100);
}

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

function getPreferredOutputTab(result) {
  if (
    result.reason_code === "COMPILE_ERROR" ||
    result.reason_code === "COMPILE_TIMEOUT"
  ) {
    return "compileLog";
  }

  if (result.stderr) {
    return "stderr";
  }

  return "stdout";
}

function getExecutionResultPresentation(result) {
  if (result.status === "SUCCESS") {
    return {
      state: "success",
      label: "성공",
      message: "코드 실행이 완료되었습니다.",
    };
  }

  const reasonPresentation = EXECUTION_RESULT_PRESENTATION[result.reason_code];

  if (reasonPresentation) {
    return {
      ...reasonPresentation,
      message: result.error_message ?? reasonPresentation.message,
    };
  }

  if (result.status === "BLOCKED") {
    return {
      state: "blocked",
      label: "정책 위반",
      message:
        result.error_message ?? "정책에 의해 코드 실행이 차단되었습니다.",
    };
  }

  return {
    state: "error",
    label: "실행 실패",
    message: result.error_message ?? "코드 실행 중 오류가 발생했습니다.",
  };
}

function getRequestErrorPresentation(error, phase) {
  const action = phase === "polling" ? "실행 상태 및 결과 조회" : "실행 요청";

  if (error instanceof ApiError) {
    if (error.type === "network") {
      return {
        code: "NETWORK_ERROR",
        label: "연결 실패",
        message: `${action} 중 네트워크 연결에 실패했습니다. Backend 실행 상태를 확인해주세요.`,
      };
    }

    if (error.type === "server") {
      const isRunnerConnectionError = [502, 503, 504].includes(error.status);

      return {
        code: isRunnerConnectionError ? "RUNNER_UNAVAILABLE" : "BACKEND_ERROR",
        label: isRunnerConnectionError ? "Runner 연결 실패" : "서버 오류",
        message: isRunnerConnectionError
          ? "Runner와 연결할 수 없습니다. 실행 환경 상태를 확인해주세요."
          : `${action} 중 Backend 내부 오류가 발생했습니다. 잠시 후 다시 시도해주세요.`,
      };
    }

    if (error.type === "invalid-response") {
      return {
        code: "INVALID_RESPONSE",
        label: "응답 오류",
        message: `${action} 응답을 올바르게 처리할 수 없습니다.`,
      };
    }

    return {
      code: "REQUEST_ERROR",
      label: "요청 실패",
      message: error.message || `${action}에 실패했습니다.`,
    };
  }

  return {
    code: "UNKNOWN_ERROR",
    label: "알 수 없는 오류",
    message:
      error instanceof Error
        ? error.message
        : `${action} 중 오류가 발생했습니다.`,
  };
}

function MainPage() {
  // 입력 및 화면 상태
  const [language, setLanguage] = useState("CPP");
  const [code, setCode] = useState(DEFAULT_CODE);
  const [standardInput, setStandardInput] = useState("");
  const [executionState, setExecutionState] = useState("idle");
  const [isEditorFullscreen, setIsEditorFullscreen] = useState(false);
  const [jobId, setJobId] = useState(null);
  const [executionResult, setExecutionResult] = useState(null);
  const [executionStatusText, setExecutionStatusText] = useState("실행 전");
  const [requestErrorCode, setRequestErrorCode] = useState(null);
  const [message, setMessage] = useState(
    "코드를 실행하면 이곳에서 결과를 확인할 수 있습니다.",
  );
  const [activeOutputTab, setActiveOutputTab] = useState("stdout");
  const selectedPolicyProfile = "basic";

  // 실행 중복 요청 방지
  const executionLockRef = useRef(false);

  // 선택값 및 실행 상태 파생 데이터
  const isExecuting = executionState === "loading";
  const selectedPolicy = ACTIVE_POLICY;

  const outputByTab = {
    stdout: executionResult?.stdout,
    stderr: executionResult?.stderr,
    compileLog: executionResult?.compile_log,
  };

  const resultOutput = executionResult
    ? outputByTab[activeOutputTab] || "출력 내용이 없습니다."
    : "아직 실행 결과가 없습니다.";

  const executionReasonCode =
    executionResult?.reason_code ?? requestErrorCode ?? "-";
  const executionExitCode = executionResult?.exit_code ?? "-";

  const isTimeLimitExceeded = executionResult?.reason_code === "TIME_LIMIT";
  const isMemoryLimitExceeded = executionResult?.reason_code === "MEMORY_LIMIT";
  const isPidsLimitExceeded = executionResult?.reason_code === "PIDS_LIMIT";

  // 실제 Runner 응답 기반 자원 사용량
  const resourceUsage = executionResult?.resource_usage;
  const wallTimeMs = resourceUsage?.wall_time_ms;
  const memoryPeakMb =
    resourceUsage?.memory_peak_bytes != null
      ? resourceUsage.memory_peak_bytes / 1024 / 1024
      : null;
  const pidsPeak = resourceUsage?.pids_peak;

  const wallTimePercentage = calculateUsagePercentage(
    wallTimeMs ?? 0,
    selectedPolicy.timeoutMs,
  );

  const memoryPercentage = calculateUsagePercentage(
    memoryPeakMb ?? 0,
    selectedPolicy.memoryLimitMb,
  );

  const pidsPercentage = calculateUsagePercentage(
    pidsPeak ?? 0,
    selectedPolicy.processLimit,
  );

  const cpuTimeMs = resourceUsage?.cpu_time_ms;
  const cpuUsagePercentage =
    cpuTimeMs != null && wallTimeMs != null
      ? calculateUsagePercentage(cpuTimeMs, wallTimeMs)
      : null;

  // 화면에 표시할 실행 단계
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

  // 실행 결과 폴링
  const pollExecution = async (currentJobId) => {
    while (true) {
      await wait(POLLING_INTERVAL_MS);

      const result = await getExecution(currentJobId);
      setExecutionResult(result);

      if (result.status === "PENDING") {
        setExecutionStatusText("대기 중");
        setMessage("실행 요청이 대기 중입니다.");
        continue;
      }

      if (result.status === "RUNNING") {
        setExecutionStatusText("실행 중");
        setMessage("코드를 실행하고 있습니다.");
        continue;
      }

      if (["SUCCESS", "ERROR", "BLOCKED"].includes(result.status)) {
        const presentation = getExecutionResultPresentation(result);

        setExecutionState(presentation.state);
        setExecutionStatusText(presentation.label);
        setMessage(presentation.message);
        setActiveOutputTab(getPreferredOutputTab(result));
        return;
      }

      throw new Error(`알 수 없는 실행 상태입니다. (${result.status})`);
    }
  };

  // 실행 요청
  const handleSubmit = async (event) => {
    event.preventDefault();

    if (executionLockRef.current) {
      return;
    }

    if (!code.trim()) {
      setExecutionState("error");
      setExecutionStatusText("입력 오류");
      setRequestErrorCode("EMPTY_CODE");
      setMessage("실행할 코드를 입력해주세요.");
      return;
    }

    executionLockRef.current = true;
    setExecutionState("loading");
    setExecutionStatusText("실행 중");
    setJobId(null);
    setExecutionResult(null);
    setRequestErrorCode(null);
    setMessage("코드 실행을 요청하고 있습니다.");

    const executionData = {
      language,
      code,
      stdin: standardInput,
      policy_profile: selectedPolicyProfile,
    };

    let errorPhase = "request";

    try {
      const response = await createExecution(executionData);

      if (!response.job_id) {
        throw new Error("실행 요청 응답에서 실행 ID를 확인할 수 없습니다.");
      }

      setJobId(response.job_id);
      setMessage(`실행 요청이 접수되었습니다. (${response.status})`);

      errorPhase = "polling";
      await pollExecution(response.job_id);
    } catch (error) {
      const errorPresentation = getRequestErrorPresentation(error, errorPhase);

      setExecutionState("error");
      setExecutionStatusText(errorPresentation.label);
      setRequestErrorCode(errorPresentation.code);
      setMessage(errorPresentation.message);
    } finally {
      executionLockRef.current = false;
    }
  };

  const handleReset = () => {
    setCode("");
    setStandardInput("");
    setExecutionState("idle");
    setExecutionStatusText("실행 전");
    setJobId(null);
    setExecutionResult(null);
    setRequestErrorCode(null);
    setMessage("코드를 실행하면 이곳에서 결과를 확인할 수 있습니다.");
    setActiveIoTab("stdin");
    setActiveOutputTab("stdout");
  };

  return (
    <form className="main-workspace" onSubmit={handleSubmit}>
      <div className="main-workspace-grid">
        {/* 코드 편집기 및 I/O 영역 */}
        <section
          className={`workspace-panel editor-section${
            isEditorFullscreen ? " editor-section-fullscreen" : ""
          }`}
        >
          <div className="workspace-panel-header editor-panel-header">
            <h2>코드 입력 및 실행</h2>

            <div className="editor-header-controls">
              <div className="language-selector">
                <label htmlFor="language">언어</label>

                <select
                  id="language"
                  value={language}
                  onChange={(event) => setLanguage(event.target.value)}
                >
                  <option value="C">C</option>
                  <option value="CPP">C++</option>
                </select>
              </div>

              <button
                className="editor-fullscreen-button"
                type="button"
                aria-label={
                  isEditorFullscreen
                    ? "코드 편집기 전체 화면 종료"
                    : "코드 편집기 전체 화면"
                }
                title={isEditorFullscreen ? "전체 화면 종료" : "전체 화면"}
                onClick={() => setIsEditorFullscreen((current) => !current)}
              >
                {isEditorFullscreen ? "×" : "⛶"}
              </button>

              <div className="editor-action-buttons">
                <button
                  className="run-button"
                  type="submit"
                  disabled={isExecuting}
                  aria-busy={isExecuting}
                >
                  {isExecuting ? "실행 중..." : "▶ 실행"}
                </button>

                <button
                  className="io-reset-button"
                  type="button"
                  disabled={isExecuting}
                  onClick={handleReset}
                >
                  ↻ 초기화
                </button>
              </div>
            </div>
          </div>

          <div className="code-editor-area">
            <CodeMirror
              className="code-editor"
              value={code}
              height="100%"
              minHeight="360px"
              extensions={[cpp()]}
              onChange={(value) => setCode(value)}
              basicSetup={{
                lineNumbers: true,
                highlightActiveLineGutter: true,
                highlightActiveLine: true,
                foldGutter: false,
                dropCursor: true,
                allowMultipleSelections: true,
                indentOnInput: true,
                bracketMatching: true,
                closeBrackets: true,
                autocompletion: true,
                rectangularSelection: true,
                crosshairCursor: false,
                highlightSelectionMatches: true,
                closeBracketsKeymap: true,
                defaultKeymap: true,
                searchKeymap: true,
                historyKeymap: true,
                foldKeymap: false,
                completionKeymap: true,
                lintKeymap: true,
              }}
              theme="light"
            />
          </div>

          <div className="io-section">
            <div className="io-input-panel">
              <label htmlFor="standard-input">표준 입력 (stdin)</label>

              <textarea
                id="standard-input"
                className="standard-input"
                value={standardInput}
                onChange={(event) => setStandardInput(event.target.value)}
                rows={4}
                placeholder="프로그램에 전달할 입력값이 있다면 작성하세요..."
                aria-label="표준 입력"
              />
            </div>

            <div className="io-output-panel">
              <div
                className="output-tabs"
                role="tablist"
                aria-label="출력 결과 종류"
              >
                <button
                  className={`output-tab${
                    activeOutputTab === "stdout" ? " active" : ""
                  }`}
                  type="button"
                  role="tab"
                  aria-selected={activeOutputTab === "stdout"}
                  onClick={() => setActiveOutputTab("stdout")}
                >
                  stdout
                </button>

                <button
                  className={`output-tab${
                    activeOutputTab === "stderr" ? " active" : ""
                  }`}
                  type="button"
                  role="tab"
                  aria-selected={activeOutputTab === "stderr"}
                  onClick={() => setActiveOutputTab("stderr")}
                >
                  stderr
                </button>

                <button
                  className={`output-tab${
                    activeOutputTab === "compileLog" ? " active" : ""
                  }`}
                  type="button"
                  role="tab"
                  aria-selected={activeOutputTab === "compileLog"}
                  onClick={() => setActiveOutputTab("compileLog")}
                >
                  컴파일 로그
                </button>
              </div>

              <pre className="io-result-output" aria-label="출력 결과">
                {resultOutput}
              </pre>
            </div>
          </div>
        </section>

        <div className="execution-overview-column">
          {/* 실행 설정 영역 */}
          <section className="workspace-panel settings-section">
            <div className="workspace-panel-header">
              <h2>현재 실행 환경</h2>
            </div>

            <div className="settings-content">
              <h3>적용 중인 제한</h3>

              <div className="environment-limit-grid">
                <div className="environment-limit-card">
                  <span className="environment-limit-icon metric-time">
                    <MetricIcon type="time" />
                  </span>
                  <div>
                    <small>시간 제한</small>
                    <strong>{selectedPolicy.timeoutMs / 1000} sec</strong>
                  </div>
                </div>

                <div className="environment-limit-card">
                  <span className="environment-limit-icon metric-memory">
                    <MetricIcon type="memory" />
                  </span>
                  <div>
                    <small>메모리</small>
                    <strong>{selectedPolicy.memoryLimitMb} MB</strong>
                  </div>
                </div>

                <div className="environment-limit-card">
                  <span className="environment-limit-icon metric-process">
                    <MetricIcon type="process" />
                  </span>
                  <div>
                    <small>PID</small>
                    <strong>{selectedPolicy.processLimit}개</strong>
                  </div>
                </div>

                <div className="environment-limit-card">
                  <span className="environment-limit-icon metric-cpu">
                    <MetricIcon type="cpu" />
                  </span>
                  <div>
                    <small>CPU</small>
                    <strong>{selectedPolicy.cpuLimit.toFixed(1)} CPU</strong>
                  </div>
                </div>
              </div>

              <h3 className="planned-feature-title">추가 예정 기능</h3>

              <div className="planned-feature-grid">
                <article className="planned-feature-item">
                  <span className="planned-feature-icon planned-file-icon">
                    <MetricIcon type="file" />
                  </span>
                  <strong>파일 접근 제한</strong>
                  <small>추가 예정</small>
                </article>

                <article className="planned-feature-item">
                  <span className="planned-feature-icon planned-network-icon">
                    <MetricIcon type="network" />
                  </span>
                  <strong>네트워크 차단</strong>
                  <small>추가 예정</small>
                </article>

                <article className="planned-feature-item">
                  <span className="planned-feature-icon planned-permission-icon">
                    <MetricIcon type="permission" />
                  </span>
                  <strong>권한 제한</strong>
                  <small>추가 예정</small>
                </article>

                <article className="planned-feature-item">
                  <span className="planned-feature-icon planned-output-icon">
                    <MetricIcon type="output" />
                  </span>
                  <strong>출력 제한</strong>
                  <small>추가 예정</small>
                </article>
              </div>
            </div>
          </section>

          {/* 실행 결과 영역 */}
          <section className="workspace-panel result-section">
            <div className="workspace-panel-header">
              <h2>실행 결과</h2>
            </div>

            <div className="execution-result-content">
              {executionState !== "idle" && (
                <p
                  className={`execution-message execution-message-${executionState}`}
                >
                  <span>
                    <ResultMessageIcon status={executionState} />
                  </span>{" "}
                  {message}
                </p>
              )}

              <div className="execution-result-meta">
                <div>
                  <span>상태</span>
                  <strong
                    className={`execution-status-badge execution-status-${executionState}`}
                    title={jobId ? `실행 ID: ${jobId}` : undefined}
                  >
                    {executionStatusText}
                  </strong>
                </div>
                <div>
                  <span>종료 코드</span>
                  <strong>{executionExitCode}</strong>
                </div>
                <div>
                  <span>종료 사유</span>
                  <strong>{executionReasonCode}</strong>
                </div>
              </div>

              <div className="execution-stage-flow" aria-label="단계별 결과">
                {executionStages.map((stage, index) => (
                  <div className="execution-stage-item" key={stage.key}>
                    <div className={`stage-indicator stage-${stage.status}`}>
                      <StatusGlyph status={stage.status} />
                    </div>
                    <strong>{stage.label}</strong>
                    <span>{stage.statusLabel.replace(/^[^ ]+ /, "")}</span>
                    {index < executionStages.length - 1 && (
                      <span className="stage-arrow" aria-hidden="true">
                        <ArrowIcon />
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </section>

          {/* 자원 사용량 요약 영역 */}
          <section className="workspace-panel resource-section">
            <div className="resource-section-header">
              <h2>자원 사용량</h2>
            </div>

            <div className="resource-card-grid">
              <article
                className={`resource-usage-card resource-wall-time${
                  isTimeLimitExceeded ? " resource-limit-exceeded" : ""
                }`}
              >
                <span>실행 시간</span>
                <strong>
                  {wallTimeMs == null
                    ? "-"
                    : `${(wallTimeMs / 1000).toFixed(3)} / ${(selectedPolicy.timeoutMs / 1000).toFixed(0)}`}
                  <small> sec</small>
                </strong>
                <div className="resource-progress">
                  <span style={{ width: `${wallTimePercentage}%` }} />
                </div>
                <em>{wallTimeMs == null ? "-" : `${wallTimePercentage}%`}</em>
              </article>

              <article
                className={`resource-usage-card resource-memory${
                  isMemoryLimitExceeded ? " resource-limit-exceeded" : ""
                }`}
              >
                <span>최대 메모리</span>
                <strong>
                  {memoryPeakMb == null
                    ? "-"
                    : `${memoryPeakMb.toFixed(1)} / ${selectedPolicy.memoryLimitMb}`}
                  <small> MB</small>
                </strong>
                <div className="resource-progress">
                  <span style={{ width: `${memoryPercentage}%` }} />
                </div>
                <em>{memoryPeakMb == null ? "-" : `${memoryPercentage}%`}</em>
              </article>

              <article
                className={`resource-usage-card resource-process${
                  isPidsLimitExceeded ? " resource-limit-exceeded" : ""
                }`}
              >
                <span>최대 PID 개수</span>
                <strong>
                  {pidsPeak == null
                    ? "-"
                    : `${pidsPeak} / ${selectedPolicy.processLimit}`}
                  <small> 개</small>
                </strong>
                <div className="resource-progress">
                  <span style={{ width: `${pidsPercentage}%` }} />
                </div>
                <em>{pidsPeak == null ? "-" : `${pidsPercentage}%`}</em>
              </article>

              <article
                className={`resource-usage-card resource-cpu${
                  cpuTimeMs == null ? " resource-disabled" : ""
                }`}
              >
                <span>CPU 사용률</span>
                <strong>
                  {cpuUsagePercentage == null
                    ? "미측정"
                    : `${cpuUsagePercentage}`}
                  {cpuUsagePercentage != null && <small>%</small>}
                </strong>
                <div className="resource-progress">
                  <span style={{ width: `${cpuUsagePercentage ?? 0}%` }} />
                </div>
                <em>
                  {cpuTimeMs == null
                    ? "측정 예정"
                    : `${cpuTimeMs.toLocaleString()} ms`}
                </em>
              </article>
            </div>
          </section>

          <section className="workspace-panel summary-section">
            <div className="workspace-panel-header">
              <h2>결과 요약</h2>
            </div>

            <div className="summary-list">
              {executionStages.map((stage) => (
                <div
                  className={`summary-item summary-${stage.status}`}
                  key={stage.key}
                >
                  <span className="summary-indicator" aria-hidden="true">
                    <StatusGlyph status={stage.status} />
                  </span>
                  <span>
                    {stage.key === "COMPILE"
                      ? "정상 컴파일 완료"
                      : stage.key === "EXECUTE"
                        ? executionState === "blocked"
                          ? "실행 차단됨"
                          : "실행 완료"
                        : "정리 완료"}
                  </span>
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>
    </form>
  );
}

export default MainPage;
