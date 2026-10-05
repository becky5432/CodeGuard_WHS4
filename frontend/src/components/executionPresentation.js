const EXECUTION_RESULT_PRESENTATION = {
  COMPILE_ERROR: {
    state: "error",
    label: "컴파일 오류",
    message: "코드를 컴파일하지 못했습니다. 컴파일 로그를 확인해주세요.",
  },
  COMPILE_TIMEOUT: {
    state: "error",
    label: "컴파일 시간 초과",
    message: "컴파일 제한 시간을 초과했습니다.",
  },
  RUNTIME_ERROR: {
    state: "error",
    label: "런타임 오류",
    message: "프로그램 실행 중 오류가 발생했습니다. stderr를 확인해주세요.",
  },
  TIME_LIMIT: {
    state: "blocked",
    label: "시간 제한 초과",
    message: "실행 시간 제한을 초과하여 실행이 중지되었습니다.",
  },
  MEMORY_LIMIT: {
    state: "blocked",
    label: "메모리 제한 초과",
    message: "메모리 제한을 초과하여 실행이 중지되었습니다.",
  },
  PIDS_LIMIT: {
    state: "blocked",
    label: "프로세스 제한 초과",
    message: "프로세스 및 스레드 제한을 초과하여 실행이 중지되었습니다.",
  },
  OUTPUT_LIMIT: {
    state: "blocked",
    label: "출력 제한 초과",
    message: "출력 제한을 초과하여 실행이 중지되었습니다.",
  },
  SECURITY_VERIFICATION_FAILED: {
    state: "error",
    label: "권한 제한 실패",
    message: "권한 검증에 실패하여 코드 실행이 중지되었습니다.",
  },
  CPU_TIME_LIMIT: {
    state: "blocked",
    label: "CPU 시간 제한 초과",
    message: "CPU 시간 제한을 초과하여 실행이 중지되었습니다.",
  },

  FILESYSTEM_LIMIT: {
    state: "blocked",
    label: "파일 접근 제한",
    message: "파일시스템 접근 정책을 위반하여 실행이 중지되었습니다.",
  },
  NETWORK_BLOCKED: {
    state: "blocked",
    label: "네트워크 차단",
    message: "허용되지 않은 네트워크 접근이 감지되어 실행이 중지되었습니다.",
  },
  INTERNAL_ERROR: {
    state: "error",
    label: "내부 오류",
    message: "실행 환경에서 내부 오류가 발생했습니다.",
  },
};

export function getExecutionResultPresentation(result) {
  if (!result) {
    return {
      state: "idle",
      label: "실행 전",
      message: "",
    };
  }
  if (result.status === "SUCCESS") {
    return {
      state: "success",
      label: "성공",
      message: "코드 실행이 완료되었습니다.",
    };
  }

  const reasonPresentation = EXECUTION_RESULT_PRESENTATION[result.reason_code];

  if (reasonPresentation) {
    return {
      ...reasonPresentation,
      message: result.error_message ?? reasonPresentation.message,
    };
  }

  if (result.status === "BLOCKED") {
    return {
      state: "blocked",
      label: "정책 위반",
      message:
        result.error_message ?? "정책에 의해 코드 실행이 차단되었습니다.",
    };
  }

  return {
    state: "error",
    label: "실행 실패",
    message: result.error_message ?? "코드 실행 중 오류가 발생했습니다.",
  };
}

export default EXECUTION_RESULT_PRESENTATION;
