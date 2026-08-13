function Header({ title, description }) {
  return (
    <header className="header">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>

      <span className="environment-status">실행 환경 준비 중</span>
    </header>
  );
}

export default Header;
