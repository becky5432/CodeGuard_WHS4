import { useEffect, useState } from "react";
function getPreferredOutputTab(result) {
  if (
    result?.reason_code === "COMPILE_ERROR" ||
    result?.reason_code === "COMPILE_TIMEOUT"
  ) {
    return "compileLog";
  }

  if (result?.stderr) {
    return "stderr";
  }

  return "stdout";
}

function OutputPanel({
  executionResult,
  showInput = false,
  standardInput = "",
  onStandardInputChange,
  readOnly = false,
}) {
  const [activeOutputTab, setActiveOutputTab] = useState("stdout");

  useEffect(() => {
    if (!executionResult) {
      setActiveOutputTab("stdout");
      return;
    }

    setActiveOutputTab(getPreferredOutputTab(executionResult));
  }, [executionResult]);

  const outputByTab = {
    stdout: executionResult?.stdout,
    stderr: executionResult?.stderr,
    compileLog: executionResult?.compile_log,
  };

  const resultOutput = executionResult
    ? outputByTab[activeOutputTab] || "출력 내용이 없습니다."
    : "아직 실행 결과가 없습니다.";

  const handleOutputKeyDown = (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
      event.preventDefault();

      const selection = window.getSelection();
      const range = document.createRange();

      range.selectNodeContents(event.currentTarget);

      selection.removeAllRanges();
      selection.addRange(range);
    }
  };

  return (
    <div className="io-section">
      {showInput && (
        <div className="io-input-panel">
          <label className="io-field-label" htmlFor="standard-input">
            표준 입력 (stdin)
          </label>

          <textarea
            id="standard-input"
            className="standard-input"
            value={standardInput}
            onChange={(event) => onStandardInputChange?.(event.target.value)}
            readOnly={readOnly}
            rows={4}
            placeholder="프로그램에 전달할 입력값이 있다면 작성하세요..."
            aria-label="표준 입력"
          />
        </div>
      )}

      <div className="io-output-panel">
        <div className="output-tabs" role="tablist" aria-label="출력 결과 종류">
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

        <pre
          className={`io-result-output${
            executionResult ? "" : " io-result-output-empty"
          }`}
          aria-label="출력 결과"
          tabIndex={0}
          onKeyDown={handleOutputKeyDown}
        >
          {resultOutput}
        </pre>
      </div>
    </div>
  );
}

export default OutputPanel;
