export function MetricIcon({ type }) {
  const iconPaths = {
    time: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3.8 2.3" />
      </>
    ),
    memory: (
      <>
        <rect x="5" y="6" width="14" height="12" rx="1.5" />
        <path d="M8 9h8M8 12h8M8 15h5M8 3v3M12 3v3M16 3v3M8 18v3M12 18v3M16 18v3" />
      </>
    ),
    process: (
      <>
        <circle cx="8.5" cy="8" r="3.2" />
        <circle cx="16.5" cy="9" r="2.7" />
        <path d="M3.5 19c.5-3 2.2-4.7 5-4.7s4.6 1.7 5.2 4.7M14.2 14.8c2.8-.6 5.4.7 6.2 3.8" />
      </>
    ),
    cpu: (
      <>
        <rect x="6" y="6" width="12" height="12" rx="1.5" />
        <rect x="9.5" y="9.5" width="5" height="5" rx="0.5" />
        <path d="M9 3v3M12 3v3M15 3v3M9 18v3M12 18v3M15 18v3M3 9h3M3 12h3M3 15h3M18 9h3M18 12h3M18 15h3" />
      </>
    ),
    cputime: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3.8 2.3" />
      </>
    ),
    file: (
      <>
        <path d="M6 3.5h7l5 5V20.5H6z" />
        <path d="M13 3.5v5h5M9 13h6M9 16h6" />
      </>
    ),
    network: (
      <>
        <circle cx="12" cy="12" r="8.5" />
        <path d="M3.8 12h16.4M12 3.5c2.1 2.3 3.2 5.1 3.2 8.5s-1.1 6.2-3.2 8.5c-2.1-2.3-3.2-5.1-3.2-8.5S9.9 5.8 12 3.5z" />
      </>
    ),
    permission: (
      <>
        <rect x="5.5" y="10" width="13" height="10" rx="2" />
        <path d="M8.5 10V7.5a3.5 3.5 0 0 1 7 0V10M12 14v2.5" />
      </>
    ),
    output: (
      <>
        <path d="M7 9V4h10v5" />
        <path d="M6 9h12a2 2 0 0 1 2 2v5H4v-5a2 2 0 0 1 2-2Z" />
        <path d="M7 16h10v4H7z" />
        <path d="M16 12h.01" />
      </>
    ),
    total: (
      <>
        <rect x="4" y="4" width="16" height="16" rx="2" />
        <path d="M8 9h8M8 12h8M8 15h5" />
      </>
    ),
    success: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="m8 12 2.7 2.7L16.5 9" />
      </>
    ),

    compile: (
      <>
        <path d="M6 3.5h7l5 5V20.5H6z" />
        <path d="M13 3.5v5h5" />
        <path d="m9 12 6 6M15 12l-6 6" />
      </>
    ),

    runtime: (
      <>
        <path d="M12 3 3.5 19h17L12 3z" />
        <path d="M12 9v4" />
        <path d="M12 16h.01" />
      </>
    ),
    internal: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M12 3v2M12 19v2M3 12h2M19 12h2" />
        <path d="m5.6 5.6 1.4 1.4M17 17l1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4" />
        <circle cx="12" cy="12" r="7" />
      </>
    ),
  };

  return (
    <svg
      className="metric-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.25"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {iconPaths[type]}
    </svg>
  );
}

export function StatusGlyph({ status }) {
  if (status === "success") {
    return (
      <svg
        className="status-glyph"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="m5 12 4.2 4.2L19 6.5" />
      </svg>
    );
  }

  if (status === "failed") {
    return (
      <svg
        className="status-glyph"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        aria-hidden="true"
      >
        <path d="m8 8 8 8M16 8l-8 8" />
      </svg>
    );
  }

  return (
    <svg
      className="status-glyph"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="7" />
      <path d="M9.5 12h5" />
    </svg>
  );
}

export function ResultMessageIcon({ status }) {
  const isSuccess = status === "success";
  const isFailure = status === "error" || status === "blocked";

  return (
    <svg
      className="alert-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" fill="currentColor" stroke="none" />
      {isSuccess && (
        <path d="m7.2 12.2 3.1 3.1 6.5-6.7" stroke="#fff" strokeWidth="2.4" />
      )}
      {isFailure && (
        <path d="m8.5 8.5 7 7M15.5 8.5l-7 7" stroke="#fff" strokeWidth="2.4" />
      )}
      {!isSuccess && !isFailure && (
        <path d="M12 7.8v5.3M12 16.4h.01" stroke="#fff" strokeWidth="2.4" />
      )}
    </svg>
  );
}
