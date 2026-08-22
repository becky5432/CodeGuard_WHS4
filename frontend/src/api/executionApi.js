const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

export class ApiError extends Error {
  constructor(message, type, status = null) {
    super(message);
    this.name = "ApiError";
    this.type = type;
    this.status = status;
  }
}

async function getErrorDetail(response) {
  try {
    const errorData = await response.json();

    if (typeof errorData.detail === "string") {
      return errorData.detail;
    }
    if (typeof errorData.message === "string") {
      return errorData.message;
    }
  } catch {
    return null;
  }
  return null;
}

async function request(path, options = {}) {
  let response;

  try {
    response = await fetch(`${API_BASE_URL}${path}`, options);
  } catch {
    throw new ApiError(
      "네트워크 연결에 실패했습니다. 연결 상태를 확인한 후 다시 시도해주세요.",
      "network",
    );
  }

  if (!response.ok) {
    const errorDetail = await getErrorDetail(response);
    if (response.status >= 500) {
      throw new ApiError(
        errorDetail ?? "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
        "server",
        response.status,
      );
    }
    throw new ApiError(
      errorDetail ?? `API 요청에 실패했습니다. (${response.status})`,
      "request",
      response.status,
    );
  }

  try {
    return await response.json();
  } catch {
    throw new ApiError(
      "서버 응답을 올바르게 처리할 수 없습니다.",
      "invalid-response",
      response.status,
    );
  }
}

export function createExecution(executionData) {
  return request("/executions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(executionData),
  });
}

export function getExecution(jobId) {
  return request(`/executions/${jobId}`);
}
