function Header({ title, description }) {
  return (
    <header className="header">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
    </header>
  );
}

export default Header;
