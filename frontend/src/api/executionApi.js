const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, options);

  if (!response.ok) {
    throw new Error(`API 요청에 실패했습니다. (${response.status})`);
  }

  return response.json();
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
