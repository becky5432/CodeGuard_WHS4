import StateMessage from "../components/StateMessage";

function ResultPage() {
  return (
    <div className="page-content">
      <section className="result-summary">
        <h2>실행 상태</h2>

        <StateMessage
          type="empty"
          title="실행 결과가 없습니다."
          description="코드를 실행하면 이곳에 컴파일 및 실행 결과가 표시됩니다."
        />
      </section>

      <section className="result-details">
        <h2>실행 정보</h2>

        <dl>
          <div>
            <dt>종료 코드</dt>
            <dd>-</dd>
          </div>

          <div>
            <dt>실행 시간</dt>
            <dd>-</dd>
          </div>

          <div>
            <dt>메모리 사용량</dt>
            <dd>-</dd>
          </div>

          <div>
            <dt>정책 판정</dt>
            <dd>-</dd>
          </div>
        </dl>
      </section>

      <section className="output-section">
        <h2>출력 내용</h2>

        <div className="output-tabs">
          <button type="button">stdout</button>
          <button type="button">stderr</button>
          <button type="button">컴파일 로그</button>
        </div>

        <pre>아직 출력 결과가 없습니다.</pre>
      </section>
    </div>
  );
}

export default ResultPage;
