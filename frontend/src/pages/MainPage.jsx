import { useRef, useState } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { cpp } from "@codemirror/lang-cpp";
import { ApiError, createExecution, getExecution } from "../api/executionApi";
import { MetricIcon } from "../components/Icons";
import ExecutionResultPanel from "../components/execution/ExecutionResultPanel";
import OutputPanel from "../components/execution/OutputPanel";
import ResourceUsagePanel from "../components/execution/ResourceUsagePanel";
import { getExecutionResultPresentation } from "../components/executionPresentation";

const DEFAULT_CODE = `#include <iostream>
using namespace std;

int main() {
    cout << "Hello, CodeGuard!" << endl;
    return 0;
}`;

const DEFAULT_POLICY = {
  timeout_ms: 2000,
  memory_limit_mb: 128,
  pids_limit: 32,
  cpu_bandwidth: 1.0,
  output_limit_bytes: 1048576,
  cpu_time_limit_ms: 1000,
};

const POLICY_FIELDS = [
  {
    key: "timeout_ms",
    label: "실행 시간",
    unit: "ms",
    type: "time",
    step: 1,
    min: 1,
    reasonCode: "TIME_LIMIT",
  },
  {
    key: "memory_limit_mb",
    label: "메모리 제한",
    unit: "MB",
    type: "memory",
    step: 1,
    min: 1,
    reasonCode: "MEMORY_LIMIT",
  },
  {
    key: "pids_limit",
    label: "PID 제한",
    unit: "개",
    type: "process",
    step: 1,
    min: 1,
    reasonCode: "PIDS_LIMIT",
  },
  {
    key: "cpu_bandwidth",
    label: "CPU 처리량",
    unit: "CPU",
    type: "cpu",
    step: "any",
    min: Number.MIN_VALUE,
    reasonCode: null,
  },
  {
    key: "cpu_time_limit_ms",
    label: "CPU 시간",
    unit: "ms",
    type: "cputime",
    step: 1,
    min: 1,
    reasonCode: "CPU_TIME_LIMIT",
  },
  {
    key: "output_limit_bytes",
    label: "출력 제한",
    unit: "bytes",
    type: "output",
    step: 1,
    min: 1,
    reasonCode: "OUTPUT_LIMIT",
  },
];

const POLLING_INTERVAL_MS = 1000;

const wait = (delay) => new Promise((resolve) => setTimeout(resolve, delay));

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

/* 실행 중 구간별 CPU 사용률 그래프 */

/* 실행 중 구간별 메모리 사용량 그래프 */

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
  const [policyInputMessage, setPolicyInputMessage] = useState(null);
  const [networkPreset, setNetworkPreset] = useState("none");

  const showPolicyInputMessage = (key) => {
    setPolicyInputMessage(key);

    setTimeout(() => {
      setPolicyInputMessage(null);
    }, 2000);
  };

  // 실행 중복 요청 방지
  const executionLockRef = useRef(false);

  // 선택값 및 실행 상태 파생 데이터
  const isExecuting = executionState === "loading";
  const [selectedPolicy, setSelectedPolicy] = useState(DEFAULT_POLICY);

  const handlePolicyChange = (field, value) => {
    if (value === "") {
      setSelectedPolicy((current) => ({
        ...current,
        [field.key]: "",
      }));
      return;
    }

    const numberValue = Number(value);

    if (!Number.isFinite(numberValue) || numberValue < field.min) {
      showPolicyInputMessage(field.key);
      return;
    }

    setSelectedPolicy((current) => ({
      ...current,
      [field.key]: value,
    }));
  };

  const handlePolicyReset = () => {
    setSelectedPolicy(DEFAULT_POLICY);
    setNetworkPreset("none");
  };

  const terminationReason = executionResult?.reason_code;

  const policyViolations = executionResult?.policy_violations ?? [];

  const hasPolicyViolation = (reasonCode) =>
    policyViolations.includes(reasonCode);

  const isLimitTriggered = (...reasonCodes) =>
    reasonCodes.includes(terminationReason);

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

    const integerPolicyKeys = [
      "timeout_ms",
      "memory_limit_mb",
      "pids_limit",
      "cpu_time_limit_ms",
      "output_limit_bytes",
    ];
    const hasInvalidPolicy = POLICY_FIELDS.some(({ key }) => {
      const value = Number(selectedPolicy[key]);

      return (
        selectedPolicy[key] === "" ||
        !Number.isFinite(value) ||
        value <= 0 ||
        (integerPolicyKeys.includes(key) && !Number.isInteger(value))
      );
    });

    if (hasInvalidPolicy) {
      setExecutionState("error");
      setExecutionStatusText("입력 오류");
      setRequestErrorCode("INVALID_POLICY");
      setMessage("정책 값은 0보다 큰 정수로 입력해주세요. (CPU 처리량 제외)");
      return;
    }

    executionLockRef.current = true;
    setExecutionState("loading");
    setExecutionStatusText("실행 중");
    //setJobId(null);
    setExecutionResult(null);
    setRequestErrorCode(null);
    setMessage("코드 실행을 요청하고 있습니다.");

    const executionData = {
      language,
      code,
      stdin: standardInput,
      policy: {
        timeout_ms: Number(selectedPolicy.timeout_ms),
        memory_limit_mb: Number(selectedPolicy.memory_limit_mb),
        pids_limit: Number(selectedPolicy.pids_limit),
        cpu_bandwidth: Number(selectedPolicy.cpu_bandwidth),
        cpu_time_limit_ms: Number(selectedPolicy.cpu_time_limit_ms),
        output_limit_bytes: Number(selectedPolicy.output_limit_bytes),
        network_preset: networkPreset,
      },
    };

    let errorPhase = "request";

    try {
      const response = await createExecution(executionData);

      if (!response.job_id) {
        throw new Error("실행 요청 응답에서 실행 ID를 확인할 수 없습니다.");
      }

      //setJobId(response.job_id);
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
    //setJobId(null);
    setExecutionResult(null);
    setRequestErrorCode(null);
    setMessage("코드를 실행하면 이곳에서 결과를 확인할 수 있습니다.");
  };

  return (
    <form className="main-workspace" onSubmit={handleSubmit} noValidate>
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
          <OutputPanel
            executionResult={executionResult}
            showInput
            standardInput={standardInput}
            onStandardInputChange={setStandardInput}
          />
        </section>

        <div className="execution-overview-column">
          {/* 실행 설정 영역 */}
          <section className="workspace-panel settings-section">
            <div className="workspace-panel-header">
              <h2>자원 제한 설정</h2>

              <button
                type="button"
                className="policy-reset-button"
                onClick={handlePolicyReset}
                disabled={isExecuting}
              >
                ↻ 초기화
              </button>
            </div>

            <div className="settings-content">
              <div className="environment-limit-grid-top policy-setting-grid">
                {POLICY_FIELDS.map((field) => (
                  <div
                    className={`environment-limit-card ${
                      field.reasonCode && isLimitTriggered(field.reasonCode)
                        ? "limit-triggered"
                        : ""
                    }`}
                    key={field.key}
                  >
                    <span
                      className={`environment-limit-icon metric-${field.type}`}
                    >
                      <MetricIcon type={field.type} />
                    </span>

                    <div>
                      <label htmlFor={`policy-${field.key}`}>
                        {field.label}
                      </label>

                      <div className="policy-input-row">
                        <input
                          id={`policy-${field.key}`}
                          type="number"
                          step={field.step}
                          min={field.min}
                          value={selectedPolicy[field.key]}
                          onKeyDown={(event) => {
                            const allowedControlKeys = [
                              "Backspace",
                              "Delete",
                              "Tab",
                              "ArrowLeft",
                              "ArrowRight",
                              "ArrowUp",
                              "ArrowDown",
                              "Home",
                              "End",
                              "Enter",
                            ];
                            if (event.ctrlKey || event.metaKey) {
                              return;
                            }

                            if (allowedControlKeys.includes(event.key)) {
                              return;
                            }

                            if (/^[0-9]$/.test(event.key)) {
                              return;
                            }

                            if (
                              field.key === "cpu_bandwidth" &&
                              event.key === "."
                            ) {
                              return;
                            }

                            event.preventDefault();
                            showPolicyInputMessage(field.key);
                          }}
                          onChange={(event) =>
                            handlePolicyChange(field, event.target.value)
                          }
                          disabled={isExecuting}
                        />
                        <span>{field.unit}</span>
                        {policyInputMessage === field.key && (
                          <div className="policy-input-tooltip">
                            0보다 큰 숫자만 입력할 수 있습니다.
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>

              <div className="fixed-control-section">
                <h3>고정 통제</h3>

                <div className="environment-limit-grid-bottom">
                  <article
                    className={`planned-feature-item ${
                      hasPolicyViolation("FILESYSTEM_LIMIT")
                        ? "limit-triggered"
                        : ""
                    }`}
                  >
                    <span className="environment-limit-icon planned-file-icon">
                      <MetricIcon type="file" />
                    </span>

                    <div className="fixed-control-info">
                      <strong>파일 접근 제한</strong>
                      <small className="limit-status-badge">제한 중</small>
                    </div>
                  </article>

                  <article
                    className={`planned-feature-item network-control-item ${
                      hasPolicyViolation("NETWORK_BLOCKED")
                        ? "limit-triggered"
                        : ""
                    }`}
                  >
                    <span className="environment-limit-icon planned-network-icon">
                      <MetricIcon type="network" />
                    </span>

                    <div className="fixed-control-info">
                      <strong>네트워크 차단</strong>
                      <small className="limit-status-badge">
                        {networkPreset === "none"
                          ? "외부 연결 허용"
                          : "외부 연결 차단"}
                      </small>
                    </div>

                    <label className="network-toggle" title="외부 인터넷 연결">
                      <input
                        type="checkbox"
                        checked={networkPreset === "web"}
                        onChange={(event) =>
                          setNetworkPreset(
                            event.target.checked ? "web" : "none",
                          )
                        }
                      />

                      <span className="network-toggle-slider" />
                    </label>
                  </article>

                  <article
                    className={`planned-feature-item ${
                      isLimitTriggered("SECURITY_VERIFICATION_FAILED")
                        ? "limit-triggered"
                        : ""
                    }`}
                  >
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

          {/* 실행 결과 영역 */}
          <ExecutionResultPanel
            executionResult={executionResult}
            executionState={executionState}
            executionStatusText={executionStatusText}
            message={message}
            requestErrorCode={requestErrorCode}
          />

          {/* 자원 사용량 요약 영역 */}
          <ResourceUsagePanel
            executionResult={executionResult}
            policy={executionResult?.policy ?? selectedPolicy}
          />
        </div>
      </div>
    </form>
  );
}

export default MainPage;
