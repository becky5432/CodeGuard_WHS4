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
    label: "compile",
  },
  {
    key: "EXECUTE",
    label: "execute",
  },
  {
    key: "CLEANUP",
    label: "cleanup",
  },
];

const POLICY_PROFILE_PREVIEW = {
  basic: {
    label: "기본",
    title: "기본 정책 프로필",
    description: "일반적인 코드 실행을 위한 기본 안전 프로필",
    timeoutMs: 1000,
    memoryLimitMb: 64,
    processLimit: 32,
    cpuLimit: 1.0,
  },
  strict: {
    label: "엄격",
    title: "엄격 정책 프로필",
    description: "더 제한된 자원으로 코드를 검사하는 강화 프로필",
    timeoutMs: 500,
    memoryLimitMb: 32,
    processLimit: 8,
    cpuLimit: 0.5,
  },
  relaxed: {
    label: "완화",
    title: "완화 정책 프로필",
    description: "자원 사용 범위를 확대한 테스트용 프로필",
    timeoutMs: 3000,
    memoryLimitMb: 256,
    processLimit: 64,
    cpuLimit: 2.0,
  },
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
  const [activeIoTab, setActiveIoTab] = useState("stdin");
  const [activeOutputTab, setActiveOutputTab] = useState("stdout");
  const [selectedPolicyProfile, setSelectedPolicyProfile] = useState("basic");

  // 실행 중복 요청 방지
  const executionLockRef = useRef(false);

  // 선택값 및 실행 상태 파생 데이터
  const isExecuting = executionState === "loading";
  const selectedPolicy = POLICY_PROFILE_PREVIEW[selectedPolicyProfile];

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

  // WORKSPACE를 제외하고 화면에 표시할 실행 단계
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
        setActiveIoTab("output");
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
          <div className="workspace-panel-header editor-toolbar">
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

              <div
                className="toolbar-policy-tabs"
                role="tablist"
                aria-label="정책 프로필"
              >
                {Object.entries(POLICY_PROFILE_PREVIEW).map(
                  ([profileKey, profile]) => (
                    <button
                      className={`toolbar-policy-tab${
                        selectedPolicyProfile === profileKey ? " active" : ""
                      }`}
                      key={profileKey}
                      type="button"
                      role="tab"
                      aria-selected={selectedPolicyProfile === profileKey}
                      onClick={() => setSelectedPolicyProfile(profileKey)}
                    >
                      {profile.label}
                    </button>
                  ),
                )}
              </div>

              <button
                className="run-button"
                type="submit"
                disabled={isExecuting}
                aria-busy={isExecuting}
              >
                {isExecuting ? "실행 중..." : "▶ 실행"}
              </button>

              <button
                className="toolbar-reset-button"
                type="button"
                disabled={isExecuting}
                onClick={handleReset}
              >
                ↻ 초기화
              </button>

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
            <div
              className="io-main-tabs"
              role="tablist"
              aria-label="입출력 영역"
            >
              <button
                className={`io-main-tab${activeIoTab === "stdin" ? " active" : ""}`}
                type="button"
                role="tab"
                aria-selected={activeIoTab === "stdin"}
                onClick={() => setActiveIoTab("stdin")}
              >
                표준 입력(stdin)
              </button>

              <button
                className={`io-main-tab${activeIoTab === "output" ? " active" : ""}`}
                type="button"
                role="tab"
                aria-selected={activeIoTab === "output"}
                onClick={() => setActiveIoTab("output")}
              >
                출력 결과
              </button>
            </div>

            <div className="io-content">
              {activeIoTab === "stdin" ? (
                <textarea
                  id="standard-input"
                  className="standard-input"
                  value={standardInput}
                  onChange={(event) => setStandardInput(event.target.value)}
                  rows="4"
                  placeholder="프로그램에 전달할 입력값이 있다면 작성하세요."
                  aria-label="표준 입력"
                />
              ) : (
                <div className="output-content">
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

                  <pre className="io-result-output">{resultOutput}</pre>
                </div>
              )}
            </div>
          </div>
        </section>

        {/* 실행 설정 영역 */}
        <section className="workspace-panel settings-section">
          <div className="workspace-panel-header">
            <h2>현재 실행 환경</h2>
          </div>

          <div className="environment-content">
            <h3>적용 중인 제한</h3>

            <div className="environment-limit-grid">
              <article className="environment-limit-item">
                <span aria-hidden="true">◷</span>
                <div>
                  <small>시간 제한</small>
                  <strong>
                    {selectedPolicy.timeoutMs.toLocaleString()} ms
                  </strong>
                </div>
              </article>

              <article className="environment-limit-item">
                <span aria-hidden="true">▦</span>
                <div>
                  <small>메모리</small>
                  <strong>{selectedPolicy.memoryLimitMb} MB</strong>
                </div>
              </article>

              <article className="environment-limit-item">
                <span aria-hidden="true">♙</span>
                <div>
                  <small>프로세스</small>
                  <strong>{selectedPolicy.processLimit}개</strong>
                </div>
              </article>

              <article className="environment-limit-item">
                <span aria-hidden="true">▣</span>
                <div>
                  <small>CPU</small>
                  <strong>{selectedPolicy.cpuLimit.toFixed(1)} CPU</strong>
                </div>
              </article>
            </div>

            <h3 className="planned-feature-title">추가 예정 기능</h3>

            <div className="planned-feature-grid">
              <article className="planned-feature-item">
                <span aria-hidden="true">♧</span>
                <strong>파일 접근 제한</strong>
                <small>준비 중</small>
              </article>

              <article className="planned-feature-item">
                <span aria-hidden="true">◎</span>
                <strong>네트워크 차단</strong>
                <small>준비 중</small>
              </article>

              <article className="planned-feature-item">
                <span aria-hidden="true">◇</span>
                <strong>privileged 비활성화</strong>
                <small>준비 중</small>
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
            <div className="execution-result-row">
              <strong>상태</strong>

              <span
                className={`execution-status-badge execution-status-${executionState}`}
                title={jobId ? `실행 ID: ${jobId}` : undefined}
              >
                {executionStatusText}
              </span>
            </div>

            {executionState !== "idle" && (
              <p
                className={`execution-message execution-message-${executionState}`}
              >
                {message}
              </p>
            )}

            <div className="execution-result-row">
              <strong>사유 코드</strong>
              <span>{executionReasonCode}</span>
            </div>

            <div className="execution-result-row">
              <strong>exit code</strong>
              <span>{executionExitCode}</span>
            </div>

            <div className="execution-stage-section">
              <h3>단계별 결과</h3>

              {executionStages.map((stage) => (
                <div className="execution-stage-row" key={stage.key}>
                  <span>{stage.label}</span>

                  <span className={`execution-stage-${stage.status}`}>
                    {stage.statusLabel}
                  </span>
                </div>
              ))}
            </div>

            <div className="execution-total-time">
              <strong>총 소요 시간</strong>
              <span>
                {wallTimeMs == null ? "-" : `${wallTimeMs.toLocaleString()} ms`}
              </span>
            </div>
          </div>
        </section>
      </div>

      {/* 자원 사용량 요약 영역 */}
      <section className="workspace-panel resource-section">
        <div className="resource-section-header">
          <h2>자원 사용량 요약</h2>
        </div>

        <div className="resource-card-grid">
          <article
            className={`resource-usage-card resource-wall-time${
              isTimeLimitExceeded ? " resource-limit-exceeded" : ""
            }`}
          >
            <div className="resource-card-main">
              <span className="resource-card-icon" aria-hidden="true">
                ◷
              </span>

              <div className="resource-card-value">
                <span>Wall Time</span>

                <strong>
                  {wallTimeMs == null ? "-" : wallTimeMs.toLocaleString()}
                  {wallTimeMs != null && <small> ms</small>}
                </strong>
              </div>
              {isTimeLimitExceeded && (
                <span className="resource-limit-badge">제한 초과</span>
              )}
            </div>
            <p>
              제한 {selectedPolicy.timeoutMs.toLocaleString()} ms (
              {wallTimePercentage}%)
            </p>
            <div
              className="resource-progress"
              role="progressbar"
              aria-label="실행 시간 사용률"
              aria-valuemin="0"
              aria-valuemax="100"
              aria-valuenow={wallTimePercentage}
            >
              <span style={{ width: `${wallTimePercentage}%` }} />
            </div>
          </article>

          <article
            className={`resource-usage-card resource-memory${
              isMemoryLimitExceeded ? " resource-limit-exceeded" : ""
            }`}
          >
            <div className="resource-card-main">
              <span className="resource-card-icon" aria-hidden="true">
                ▦
              </span>

              <div className="resource-card-value">
                <span>Memory Peak</span>

                <strong>
                  {memoryPeakMb == null ? "-" : memoryPeakMb.toFixed(1)}
                  {memoryPeakMb != null && <small> MB</small>}
                </strong>
              </div>

              {isMemoryLimitExceeded && (
                <span className="resource-limit-badge">제한 초과</span>
              )}
            </div>

            <p>
              제한 {selectedPolicy.memoryLimitMb} MB ({memoryPercentage}%)
            </p>

            <div
              className="resource-progress"
              role="progressbar"
              aria-label="메모리 사용률"
              aria-valuemin="0"
              aria-valuemax="100"
              aria-valuenow={memoryPercentage}
            >
              <span style={{ width: `${memoryPercentage}%` }} />
            </div>
          </article>

          <article
            className={`resource-usage-card resource-process${
              isPidsLimitExceeded ? " resource-limit-exceeded" : ""
            }`}
          >
            <div className="resource-card-main">
              <span className="resource-card-icon" aria-hidden="true">
                ♙
              </span>

              <div className="resource-card-value">
                <span>PIDs Peak</span>

                <strong>
                  {pidsPeak == null ? "-" : pidsPeak.toLocaleString()}
                  {pidsPeak != null && <small> 개</small>}
                </strong>
              </div>

              {isPidsLimitExceeded && (
                <span className="resource-limit-badge">제한 초과</span>
              )}
            </div>
            <p>
              제한 {selectedPolicy.processLimit}개 ({pidsPercentage}%)
            </p>
            <div
              className="resource-progress"
              role="progressbar"
              aria-label="프로세스 사용률"
              aria-valuemin="0"
              aria-valuemax="100"
              aria-valuenow={pidsPercentage}
            >
              <span style={{ width: `${pidsPercentage}%` }} />
            </div>
          </article>
        </div>
      </section>
    </form>
  );
}

export default MainPage;
