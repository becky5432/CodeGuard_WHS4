import "./App.css";

function App() {
  return (
    <main className="container">
      <h1>CodeGuard</h1>
      <p>리눅스 기반 안전한 코드 실행 서비스</p>

      <section className="code-section">
        <h2>코드 입력</h2>

        <textarea placeholder="실행할 C/C++ 코드를 입력하세요." rows="15" />

        <button type="button">실행</button>
      </section>
    </main>
  );
}

export default App;
