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

const DEFAULT_POLICY = {
  timeout_ms: 2000,
  memory_limit_mb: 128,
  pids_limit: 10,
  cpu_limit: 1.0,
};

// 실행 설정 UI 확인을 위한 임시 프로필 값
// 실제 요청값은 API 연결 단계에서 최신 명세에 맞춰 교체
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
const wait = (delay) => new Promise((resolve) => setTimeout(resolve, delay));

function getExecutionErrorMessage(error, phase) {
  const action = phase === "polling" ? "실행 상태 및 결과 조회" : "실행 요청";

  if (error instanceof ApiError) {
    if (error.type === "network") {
      return `${action} 중 네트워크 연결에 실패했습니다. 연결 상태를 확인한 후 다시 시도해주세요.`;
    }

    if (error.type === "server") {
      return `${action} 중 서버 오류가 발생했습니다. 잠시 후 다시 시도해주세요.`;
    }

    if (error.type === "invalid-response") {
      return `${action} 응답을 올바르게 처리할 수 없습니다.`;
    }

    return `${action}에 실패했습니다.${
      error.status ? ` (${error.status})` : ""
    }`;
  }

  return error instanceof Error
    ? error.message
    : `${action} 중 오류가 발생했습니다.`;
}

function MainPage() {
  const [language, setLanguage] = useState("CPP");
  const [code, setCode] = useState(DEFAULT_CODE);
  const [standardInput, setStandardInput] = useState("");
  const [executionState, setExecutionState] = useState("idle");
  const [isEditorFullscreen, setIsEditorFullscreen] = useState(false);
  const [jobId, setJobId] = useState(null);
  const [executionResult, setExecutionResult] = useState(null);
  const [message, setMessage] = useState(
    "코드를 실행하면 이곳에서 결과 를 확인할 수 있습니다.",
  );
  const [activeIoTab, setActiveIoTab] = useState("stdin");
  const [activeOutputTab, setActiveOutputTab] = useState("stdout");
  const [selectedPolicyProfile, setSelectedPolicyProfile] = useState("basic");
  const executionLockRef = useRef(false);
  const isExecuting = executionState === "loading";
  const selectedPolicy = POLICY_PROFILE_PREVIEW[selectedPolicyProfile];

  const executionStatusLabel = {
    idle: "실행 전",
    loading: "실행 중",
    success: "성공",
    error: "실행 실패",
    blocked: "정책 위반",
  }[executionState];

  const outputByTab = {
    stdout: executionResult?.stdout,
    stderr: executionResult?.stderr,
    compileLog: executionResult?.compile_log,
  };

  const resultOutput = executionResult
    ? outputByTab[activeOutputTab] || "출력 내용이 없습니다."
    : "아직 실행 결과가 없습니다.";
  const executionReasonCode = executionResult?.reason_code ?? "-";

  const executionExitCode = executionResult?.exit_code ?? "-";

  const totalExecutionTime = executionResult?.resource_usage?.wall_time_ms ?? 0;

  const pollExecution = async (currentJobId) => {
    while (true) {
      await wait(POLLING_INTERVAL_MS);

      const result = await getExecution(currentJobId);
      setExecutionResult(result);

      if (result.status === "PENDING") {
        setMessage("실행 요청이 대기 중입니다.");
        continue;
      }

      if (result.status === "RUNNING") {
        setMessage("코드를 실행하고 있습니다.");
        continue;
      }

      if (result.status === "SUCCESS") {
        setExecutionState("success");
        setMessage("코드 실행이 완료되었습니다.");
        return;
      }

      if (result.status === "ERROR") {
        setExecutionState("error");
        setMessage(result.error_message ?? "코드 실행 중 오류가 발생했습니다.");
        return;
      }

      if (result.status === "BLOCKED") {
        setExecutionState("blocked");
        setMessage("정책에 의해 코드 실행이 차단되었습니다.");
        return;
      }

      throw new Error(`알 수 없는 실행 상태입니다. (${result.status})`);
    }
  };
  const handleSubmit = async (event) => {
    event.preventDefault();

    if (executionLockRef.current) {
      return;
    }

    if (!code.trim()) {
      setExecutionState("error");
      setMessage("실행할 코드를 입력해주세요.");
      return;
    }

    executionLockRef.current = true;
    setExecutionState("loading");
    setJobId(null);
    setExecutionResult(null);
    setMessage("코드 실행을 요청하고 있습니다.");

    const executionData = {
      language,
      code,
      stdin: standardInput,
      policy_profile: "basic",
      policy: DEFAULT_POLICY,
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
      setExecutionState("error");
      setMessage(getExecutionErrorMessage(error, errorPhase));
    } finally {
      executionLockRef.current = false;
    }
  };

  return (
    <form className="main-workspace" onSubmit={handleSubmit}>
      <div className="main-workspace-grid">
        <section
          className={`workspace-panel editor-section${
            isEditorFullscreen ? " editor-section-fullscreen" : ""
          }`}
        >
          <div className="workspace-panel-header">
            <h2>코드 편집기</h2>

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

            <button
              className="io-reset-button"
              type="button"
              disabled={isExecuting}
              onClick={() => {
                setCode("");
                setStandardInput("");
                setExecutionState("idle");
                setJobId(null);
                setExecutionResult(null);
                setMessage(
                  "코드를 실행하면 이곳에서 결과를 확인할 수 있습니다.",
                );
                setActiveIoTab("stdin");
                setActiveOutputTab("stdout");
              }}
            >
              ↻ 초기화
            </button>
          </div>
        </section>

        <section className="workspace-panel settings-section">
          <div className="workspace-panel-header">
            <h2>실행 설정</h2>
          </div>

          <div className="settings-content">
            <h3>정책 프로필</h3>

            <div
              className="policy-profile-tabs"
              role="tablist"
              aria-label="정책 프로필"
            >
              {Object.entries(POLICY_PROFILE_PREVIEW).map(
                ([profileKey, profile]) => (
                  <button
                    className={`policy-profile-tab${
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

            <div className="policy-profile-card">
              <div className="policy-profile-description">
                <strong>{selectedPolicy.title}</strong>
                <p>{selectedPolicy.description}</p>
              </div>

              <dl className="policy-limit-list">
                <div>
                  <dt>실행 시간</dt>
                  <dd>{selectedPolicy.timeoutMs.toLocaleString()} ms</dd>
                </div>

                <div>
                  <dt>메모리</dt>
                  <dd>{selectedPolicy.memoryLimitMb} MB</dd>
                </div>

                <div>
                  <dt>프로세스</dt>
                  <dd>{selectedPolicy.processLimit}개</dd>
                </div>

                <div>
                  <dt>CPU 한도</dt>
                  <dd>{selectedPolicy.cpuLimit.toFixed(1)} CPU</dd>
                </div>
              </dl>
            </div>
          </div>

          <button
            className="run-button"
            type="submit"
            disabled={isExecuting}
            aria-busy={isExecuting}
          >
            {isExecuting ? "실행 중..." : "실행"}
          </button>
        </section>

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
                {executionStatusLabel}
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

              <div className="execution-stage-row">
                <span>compile</span>
                <span className="execution-stage-waiting">- 대기</span>
              </div>

              <div className="execution-stage-row">
                <span>execute</span>
                <span className="execution-stage-waiting">- 대기</span>
              </div>

              <div className="execution-stage-row">
                <span>cleanup</span>
                <span className="execution-stage-waiting">- 대기</span>
              </div>
            </div>

            <div className="execution-total-time">
              <strong>총 소요 시간</strong>
              <span>{totalExecutionTime.toLocaleString()} ms</span>
            </div>
          </div>
        </section>
      </div>

      <section className="workspace-panel resource-section">
        <div className="resource-section-header">
          <h2>자원 사용량 요약</h2>
        </div>

        <div className="resource-placeholder">
          <article>
            <span>Wall Time</span>
            <strong>-</strong>
          </article>

          <article>
            <span>Memory Peak</span>
            <strong>-</strong>
          </article>

          <article>
            <span>Process Peak</span>
            <strong>-</strong>
          </article>
        </div>
      </section>
    </form>
  );
}

export default MainPage;
