const STATE_CONFIG = {
  loading: {
    icon: "⏳",
    defaultTitle: "처리 중입니다.",
  },
  error: {
    icon: "!",
    defaultTitle: "오류가 발생했습니다.",
  },
  empty: {
    icon: "○",
    defaultTitle: "표시할 데이터가 없습니다.",
  },
  info: {
    icon: "i",
    defaultTitle: "안내",
  },
};

function StateMessage({ type = "info", title, description }) {
  const config = STATE_CONFIG[type] ?? STATE_CONFIG.info;
  const isError = type === "error";

  return (
    <div
      className={`state-message state-message-${type}`}
      role={isError ? "alert" : "status"}
      aria-live={isError ? "assertive" : "polite"}
    >
      <span className="state-message-icon" aria-hidden="true">
        {config.icon}
      </span>

      <div className="state-message-content">
        <strong>{title ?? config.defaultTitle}</strong>

        {description && <p>{description}</p>}
      </div>
    </div>
  );
}

export default StateMessage;
