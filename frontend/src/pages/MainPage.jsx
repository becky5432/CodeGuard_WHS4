import { useState } from "react";

const DEFAULT_CODE = `#include <iostream>
using namespace std;

int main() {
    cout << "Hello, CodeGuard!" << endl;
    return 0;
}`;

function MainPage() {
  const [language, setLanguage] = useState("cpp");
  const [code, setCode] = useState(DEFAULT_CODE);
  const [standardInput, setStandardInput] = useState("");
  const [policyProfile, setPolicyProfile] = useState("default");
  const [message, setMessage] = useState(
    "코드를 실행하면 이곳에서 결과를 확인할 수 있습니다.",
  );

  const handleSubmit = (event) => {
    event.preventDefault();

    if (!code.trim()) {
      setMessage("실행할 코드를 입력해주세요.");
      return;
    }

    setMessage(
      "현재는 화면 구현 단계입니다. 실행 API는 추후 연결할 예정입니다.",
    );
  };

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="service-logo">
          <div className="logo-symbol">C</div>

          <div>
            <strong>CodeGuard</strong>
            <span>Linux Sandbox for C/C++</span>
          </div>
        </div>

        <nav className="navigation" aria-label="주요 메뉴">
          <button className="navigation-item active" type="button">
            코드 입력 및 실행
          </button>

          <button className="navigation-item" type="button">
            실행 기록
          </button>

          <button className="navigation-item" type="button">
            정책 안내
          </button>

          <button className="navigation-item" type="button">
            대시보드
          </button>

          <button className="navigation-item" type="button">
            리포트
          </button>
        </nav>

        <div className="service-status">
          <strong>서비스 상태</strong>
          <p>
            <span className="status-dot" />
            실행 환경 준비 중
          </p>
        </div>
      </aside>

      <div className="page">
        <header className="header">
          <div>
            <h1>코드 입력 및 실행</h1>
            <p>C/C++ 코드를 안전한 리눅스 환경에서 실행합니다.</p>
          </div>

          <span className="environment-status">실행 환경 준비 중</span>
        </header>

        <main className="main-content">
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
                  <option value="c">C</option>
                  <option value="cpp">C++</option>
                </select>
              </div>

              <div className="editor-setting">
                <label htmlFor="policy-profile">정책 프리셋</label>

                <select
                  id="policy-profile"
                  value={policyProfile}
                  onChange={(event) => setPolicyProfile(event.target.value)}
                >
                  <option value="default">기본 정책</option>
                  <option value="strict">강화 정책</option>
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
                  onClick={() => {
                    setCode("");
                    setStandardInput("");
                    setMessage(
                      "코드를 실행하면 이곳에서 결과를 확인할 수 있습니다.",
                    );
                  }}
                >
                  초기화
                </button>

                <button className="run-button" type="submit">
                  실행
                </button>
              </div>
            </section>

            <section className="result-card">
              <div className="card-header">
                <h3>실행 결과</h3>
                <span className="waiting-badge">실행 전</span>
              </div>

              <div className="result-placeholder">
                <strong>결과 대기 중</strong>
                <p>{message}</p>
              </div>

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

              <pre className="result-output">아직 실행 결과가 없습니다.</pre>
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
        </main>
      </div>
    </div>
  );
}

export default MainPage;
