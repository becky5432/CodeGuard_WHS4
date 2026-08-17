import { useRef, useState } from "react";
import StateMessage from "../components/StateMessage";
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
  process_limit: 10,
  cpu_limit: 1.0,
};

const POLLING_INTERVAL_MS = 1000;
const wait = (delay) => new Promise((resolve) => setTimeout(resolve, delay));

function getExecutionErrorMessage(error, phase) {
  const action = phase === "polling" ? "실행 상태 및 결과 조회" : "실행 요청";

  if (error instanceof ApiError) {
    if (error.type === "network") {
      return `${action}을 위한 네트워크 연결에 실패했습니다. 연결 상태를 확인한 후 다시 시도해주세요.`;
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
  const [jobId, setJobId] = useState(null);
  const [executionResult, setExecutionResult] = useState(null);
  const [message, setMessage] = useState(
    "코드를 실행하면 이곳에서 결과를 확인할 수 있습니다.",
  );

  const executionLockRef = useRef(false);

  const isExecuting = executionState === "loading";

  const executionStatusLabel = {
    idle: "실행 전",
    loading: "실행 중",
    success: "실행 완료",
    error: "실행 실패",
    blocked: "실행 차단",
  }[executionState];

  const stateMessageType =
    executionState === "error" || executionState === "blocked"
      ? "error"
      : executionState === "loading"
        ? "loading"
        : "info";

  const stateMessageTitle = {
    idle: "결과 대기 중",
    loading: "실행 요청 중",
    success: "실행 완료",
    error: "오류 발생",
    blocked: "실행 차단",
  }[executionState];

  const resultOutput =
    executionResult?.stdout ||
    executionResult?.stderr ||
    "아직 실행 결과가 없습니다.";

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
    <>
      <section className="page-introduction">
        <div>
          <h2>코드 실행</h2>
          <p>실행할 코드와 입력값을 작성한 후 실행 버튼을 눌러주세요.</p>
        </div>
      </section>

      <form className="execution-layout" onSubmit={handleSubmit}>
        <section className="editor-card">
          <div className="card-header">
            <label htmlFor="language">실행 언어</label>

            <select
              id="language"
              value={language}
              onChange={(event) => setLanguage(event.target.value)}
            >
              <option value="C">C</option>
              <option value="CPP">C++</option>
            </select>
          </div>

          <label className="field-label" htmlFor="source-code">
            소스 코드
          </label>

          <textarea
            id="source-code"
            className="code-editor"
            value={code}
            onChange={(event) => setCode(event.target.value)}
            spellCheck="false"
            rows="18"
            placeholder="실행할 C/C++ 코드를 입력하세요."
          />

          <label className="field-label" htmlFor="standard-input">
            표준 입력(stdin)
          </label>

          <textarea
            id="standard-input"
            className="standard-input"
            value={standardInput}
            onChange={(event) => setStandardInput(event.target.value)}
            rows="4"
            placeholder="프로그램에 전달할 입력값이 있다면 작성하세요."
          />

          <div className="editor-actions">
            <button
              className="secondary-button"
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
              }}
            >
              초기화
            </button>

            <button
              className="run-button"
              type="submit"
              disabled={isExecuting}
              aria-busy={isExecuting}
            >
              {isExecuting ? "실행 중..." : "실행"}
            </button>
          </div>
        </section>

        <section className="result-card">
          <div className="card-header">
            <h3>실행 결과</h3>
            <span
              className="waiting-badge"
              title={jobId ? `실행 ID: ${jobId}` : undefined}
            >
              {executionStatusLabel}
            </span>
          </div>
          <StateMessage
            type={stateMessageType}
            title={stateMessageTitle}
            description={message}
          />
          <div className="result-tabs">
            <button className="result-tab active" type="button">
              stdout
            </button>

            <button className="result-tab" type="button">
              stderr
            </button>

            <button className="result-tab" type="button">
              컴파일 로그
            </button>
          </div>
          <pre className="result-output">{resultOutput}</pre>
        </section>
      </form>

      <section className="policy-summary">
        <article>
          <span>실행 시간</span>
          <strong>제한 예정</strong>
        </article>

        <article>
          <span>메모리</span>
          <strong>제한 예정</strong>
        </article>

        <article>
          <span>프로세스</span>
          <strong>제한 예정</strong>
        </article>

        <article>
          <span>네트워크</span>
          <strong>차단 예정</strong>
        </article>
      </section>
    </>
  );
}

export default MainPage;
