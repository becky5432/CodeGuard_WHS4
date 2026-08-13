function HistoryPage() {
  return (
    <main className="page-content">
      <section className="history-section">
        <div className="history-header">
          <h2>실행 내역</h2>

          <select aria-label="실행 상태 필터">
            <option value="all">전체 상태</option>
            <option value="success">정상 종료</option>
            <option value="failed">실행 실패</option>
            <option value="blocked">정책 제한</option>
          </select>
        </div>

        <div className="empty-state">
          <strong>실행 기록이 없습니다.</strong>
          <p>코드를 실행하면 이곳에 실행 기록이 표시됩니다.</p>
        </div>
      </section>
    </main>
  );
}

export default HistoryPage;
