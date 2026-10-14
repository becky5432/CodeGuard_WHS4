import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let server;
let ExecutionResultPanel;

before(async () => {
  server = await createServer({ server: { middlewareMode: true }, appType: "custom" });
  ({ default: ExecutionResultPanel } = await server.ssrLoadModule("/src/components/execution/ExecutionResultPanel.jsx"));
});
after(async () => { await server?.close(); });

const denied = {
  status: "ERROR",
  reason_code: "RUNTIME_ERROR",
  error_message: "프로그램 실행 중 오류가 발생했습니다.",
  exit_code: 1,
  policy_violations: ["FILESYSTEM_LIMIT"],
  stage_summary: { succeeded: ["COMPILE"], failed: ["EXECUTE"] },
};
const render = (executionResult, props = {}) => renderToStaticMarkup(createElement(ExecutionResultPanel, { executionResult, ...props }));

test("the actual panel shows one file-denial notice and keeps the exit evidence", () => {
  const html = render(denied);
  assert.equal(html.split("파일 접근이 차단되었습니다.").length - 1, 1);
  assert.match(html, /파일 접근 제한/);
  assert.doesNotMatch(html, /프로그램 실행 중 오류가 발생했습니다|런타임 오류/);
  assert.match(html, /종료 코드<\/span><strong>1<\/strong>/);
  assert.match(html, /프로그램 종료 사유<\/span><strong>RUNTIME_ERROR<\/strong>/);
  assert.match(html, /실행<\/strong><span>실패/);
});

test("main-page presentation props and detail-page defaults agree", () => {
  const html = render(denied, { executionState: "blocked", executionStatusText: "파일 접근 제한", message: "파일 접근이 차단되었습니다." });
  assert.equal(html, render(denied));
});

test("a caller-provided request error does not hide the additional denial notice", () => {
  const html = render(denied, { executionState: "error", executionStatusText: "요청 오류", message: "결과 조회에 실패했습니다." });
  assert.match(html, /결과 조회에 실패했습니다/);
  assert.equal(html.split("파일 접근이 차단되었습니다.").length - 1, 1);
});

test("a successful exit after denial is reported without claiming the program was killed", () => {
  const html = render({ ...denied, status: "SUCCESS", reason_code: null, exit_code: 0, stage_summary: { succeeded: ["COMPILE", "EXECUTE"] } });
  assert.match(html, /파일 접근 제한/);
  assert.match(html, /종료 코드<\/span><strong>0<\/strong>/);
  assert.match(html, /실행<\/strong><span>성공/);
  assert.doesNotMatch(html, /실행이 중지|런타임 오류/);
});

test("secondary network denial is retained without duplicate primary notices", () => {
  const html = render({ ...denied, policy_violations: ["NETWORK_BLOCKED", "FILESYSTEM_LIMIT"] });
  assert.equal(html.split("파일 접근이 차단되었습니다.").length - 1, 1);
  assert.equal(html.split("네트워크 접근이 차단되었습니다.").length - 1, 1);
});

test("resource limits and additional file-denial notices both remain visible", () => {
  const html = render({ ...denied, status: "BLOCKED", reason_code: "TIME_LIMIT", error_message: "실행 시간 제한을 초과했습니다." });
  assert.match(html, /시간 제한 초과/);
  assert.match(html, /실행 시간 제한을 초과했습니다/);
  assert.match(html, /파일 접근이 차단되었습니다/);
});

test("a genuine runtime error without denial still has its error banner", () => {
  const html = render({ ...denied, policy_violations: [] });
  assert.match(html, /런타임 오류/);
  assert.match(html, /프로그램 실행 중 오류가 발생했습니다/);
  assert.doesNotMatch(html, /파일 접근이 차단되었습니다/);
});
