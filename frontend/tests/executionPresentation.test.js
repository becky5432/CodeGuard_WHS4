import assert from "node:assert/strict";
import test from "node:test";
import { getExecutionResultPresentation } from "../src/components/executionPresentation.js";

const denied = {
  status: "ERROR",
  reason_code: "RUNTIME_ERROR",
  error_message: "프로그램 실행 중 오류가 발생했습니다.",
  exit_code: 1,
  policy_violations: ["FILESYSTEM_LIMIT"],
};

test("file denial is the primary notice for a nonzero exit without rewriting evidence", () => {
  const result = structuredClone(denied);
  assert.equal(getExecutionResultPresentation(result).label, "파일 접근 제한");
  assert.equal(getExecutionResultPresentation(result).state, "blocked");
  assert.equal(getExecutionResultPresentation(result).message, "파일 접근이 차단되었습니다.");
  assert.deepEqual(result, denied);
});

test("a handled denial stays visible after a successful exit", () => {
  const result = { ...denied, status: "SUCCESS", reason_code: null, exit_code: 0 };
  const presentation = getExecutionResultPresentation(result);
  assert.equal(presentation.label, "파일 접근 제한");
  assert.doesNotMatch(presentation.message, /실행이 중지/);
});

test("ordinary runtime errors retain their original message", () => {
  const result = { ...denied, policy_violations: [] };
  assert.equal(getExecutionResultPresentation(result).label, "런타임 오류");
  assert.equal(getExecutionResultPresentation(result).message, result.error_message);
});

test("success without a recognized violation stays successful", () => {
  for (const policy_violations of [undefined, [], ["UNKNOWN_POLICY"]]) {
    assert.equal(getExecutionResultPresentation({ status: "SUCCESS", policy_violations }).state, "success");
  }
});

for (const reason_code of [
  "INTERNAL_ERROR", "SECURITY_VERIFICATION_FAILED", "COMPILE_ERROR", "COMPILE_TIMEOUT",
  "TIME_LIMIT", "CPU_TIME_LIMIT", "MEMORY_LIMIT", "PIDS_LIMIT", "OUTPUT_LIMIT",
]) {
  test(`${reason_code} retains priority over a policy notice`, () => {
    const result = { ...denied, reason_code };
    const withViolation = getExecutionResultPresentation(result);
    const withoutViolation = getExecutionResultPresentation({ ...result, policy_violations: [] });
    assert.deepEqual(withViolation, withoutViolation);
  });
}

test("network denial uses the same notice rule without claiming termination", () => {
  const presentation = getExecutionResultPresentation({ ...denied, policy_violations: ["NETWORK_BLOCKED"] });
  assert.equal(presentation.label, "네트워크 차단");
  assert.equal(presentation.message, "네트워크 접근이 차단되었습니다.");
});

test("multiple violations have stable primary priority regardless of array order", () => {
  for (const policy_violations of [["NETWORK_BLOCKED", "FILESYSTEM_LIMIT"], ["FILESYSTEM_LIMIT", "NETWORK_BLOCKED"]]) {
    assert.equal(getExecutionResultPresentation({ ...denied, policy_violations }).label, "파일 접근 제한");
  }
});

test("pending execution and unknown errors are not replaced by a policy notice", () => {
  for (const result of [
    { ...denied, status: "RUNNING" },
    { ...denied, status: "PENDING" },
    { ...denied, reason_code: "UNKNOWN_ERROR" },
  ]) {
    assert.deepEqual(getExecutionResultPresentation(result), getExecutionResultPresentation({ ...result, policy_violations: [] }));
  }
  assert.equal(getExecutionResultPresentation(null).state, "idle");
});
