import { useState } from "react";

import CpuUsageChart from "./CpuUsageChart";
import MemoryUsageChart from "./MemoryUsageChart";

function calculateUsagePercentage(value, limit) {
  if (!value || !limit) {
    return 0;
  }

  return Math.min(Math.round((value / limit) * 100), 100);
}

function ResourceUsagePanel({ executionResult, policy }) {
  const [fullscreenChart, setFullscreenChart] = useState(null);

  const resourceUsage = executionResult?.resource_usage;

  const terminationReason = executionResult?.reason_code;

  const wallTimeMs = resourceUsage?.wall_time_ms;

  const memoryPeakMb =
    resourceUsage?.memory_peak_bytes != null
      ? resourceUsage.memory_peak_bytes / 1024 / 1024
      : null;

  const pidsPeak = resourceUsage?.pids_peak;

  const processPeak = resourceUsage?.process_at_user_task_peak;

  const threadPeak = resourceUsage?.thread_at_user_task_peak;

  const cpuTimeMs = resourceUsage?.cpu_time_ms;

  const outputBytes = resourceUsage?.output_bytes;

  const timeoutMs = policy?.timeout_ms ?? 0;

  const memoryLimitMb = policy?.memory_limit_mb ?? 0;

  const pidsLimit = policy?.pids_limit ?? 0;

  const cpuTimeLimitMs = policy?.cpu_time_limit_ms ?? 0;

  const outputLimitBytes = policy?.output_limit_bytes ?? 0;

  const wallTimePercentage = calculateUsagePercentage(
    wallTimeMs ?? 0,
    timeoutMs,
  );

  const memoryPercentage = calculateUsagePercentage(
    memoryPeakMb ?? 0,
    memoryLimitMb,
  );

  const pidsPercentage = calculateUsagePercentage(pidsPeak ?? 0, pidsLimit);

  const cpuTimePercentage =
    cpuTimeMs == null || !cpuTimeLimitMs
      ? 0
      : Math.min(Math.round((cpuTimeMs / cpuTimeLimitMs) * 100), 100);

  const outputPercentage =
    outputBytes == null || !outputLimitBytes
      ? 0
      : Math.min(Math.round((outputBytes / outputLimitBytes) * 100), 100);

  const processPercentage =
    processPeak == null || !pidsLimit
      ? 0
      : Math.min(Math.round((processPeak / pidsLimit) * 100), 100);

  const threadPercentage =
    threadPeak == null || !pidsLimit
      ? 0
      : Math.min(Math.round((threadPeak / pidsLimit) * 100), 100);

  const isTimeLimitExceeded = terminationReason === "TIME_LIMIT";

  const isMemoryLimitExceeded = terminationReason === "MEMORY_LIMIT";

  const isPidsLimitExceeded = terminationReason === "PIDS_LIMIT";

  const isOutputLimitExceeded = terminationReason === "OUTPUT_LIMIT";

  const isCpuTimeLimitExceeded = terminationReason === "CPU_TIME_LIMIT";

  return (
    <section className="workspace-panel resource-section">
      <div className="resource-section-header">
        <h2>자원 사용량</h2>
      </div>

      <div className="resource-card-grid">
        <article
          className={`resource-usage-card resource-wall-time${
            isTimeLimitExceeded ? " resource-limit-exceeded" : ""
          }`}
        >
          <span>실행 시간</span>

          <strong>
            {wallTimeMs == null ? (
              "측정 전"
            ) : (
              <>
                {(wallTimeMs / 1000).toFixed(3)}
                {timeoutMs ? (
                  <>
                    {" / "}
                    {(timeoutMs / 1000).toFixed(0)}
                  </>
                ) : null}
                <small> sec</small>
              </>
            )}
          </strong>

          <div className="resource-progress-row">
            <div className="resource-progress">
              <span
                style={{
                  width: `${wallTimePercentage}%`,
                }}
              />
            </div>

            {wallTimeMs != null && <em>{wallTimePercentage}%</em>}
          </div>
        </article>

        <article
          className={`resource-usage-card resource-memory${
            isMemoryLimitExceeded ? " resource-limit-exceeded" : ""
          }`}
        >
          <span>최대 메모리</span>

          <strong>
            {memoryPeakMb == null ? (
              "측정 전"
            ) : (
              <>
                {memoryPeakMb.toFixed(1)}
                {memoryLimitMb ? <> / {memoryLimitMb}</> : null}
                <small> MB</small>
              </>
            )}
          </strong>

          <div className="resource-progress-row">
            <div className="resource-progress">
              <span
                style={{
                  width: `${memoryPercentage}%`,
                }}
              />
            </div>

            {memoryPeakMb != null && <em>{memoryPercentage}%</em>}
          </div>
        </article>

        <article
          className={`resource-usage-card resource-process${
            isPidsLimitExceeded ? " resource-limit-exceeded" : ""
          }`}
        >
          <div className="resource-pids-heading">
            <div>
              <span>최대 PIDs 개수</span>

              <strong>
                {pidsPeak == null ? (
                  "측정 전"
                ) : (
                  <>
                    {pidsPeak}
                    {pidsLimit ? <> / {pidsLimit}</> : null}
                    <small> 개</small>
                  </>
                )}
              </strong>
            </div>
          </div>

          <div className="resource-progress-row resource-pids-progress-row">
            <div className="resource-progress resource-pids-total">
              <span
                style={{
                  width: `${pidsPercentage}%`,
                }}
              />
            </div>

            {pidsPeak != null && <em>{pidsPercentage}%</em>}
          </div>

          <div className="resource-pids-detail">
            <span>Process</span>

            <div className="resource-progress resource-process-detail">
              <span
                style={{
                  width: `${processPercentage}%`,
                }}
              />
            </div>

            {processPeak != null && <em>{processPeak}개</em>}
          </div>

          <div className="resource-pids-detail">
            <span>Thread</span>

            <div className="resource-progress resource-thread-detail">
              <span
                style={{
                  width: `${threadPercentage}%`,
                }}
              />
            </div>

            {threadPeak != null && <em>{threadPeak}개</em>}
          </div>
        </article>

        <article
          className={`resource-usage-card resource-output${
            isOutputLimitExceeded ? " resource-limit-exceeded" : ""
          }`}
        >
          <span>출력량</span>

          <strong>
            {outputBytes == null ? (
              "측정 전"
            ) : (
              <>
                {(outputBytes / 1024).toFixed(1)}
                {outputLimitBytes ? (
                  <>
                    {" / "}
                    {(outputLimitBytes / 1024).toFixed(0)}
                  </>
                ) : null}
                <small> KB</small>
              </>
            )}
          </strong>

          <div className="resource-progress-row">
            <div className="resource-progress">
              <span
                style={{
                  width: `${outputPercentage}%`,
                }}
              />
            </div>

            {outputBytes != null && <em>{outputPercentage}%</em>}
          </div>
        </article>

        <article
          className={`resource-usage-card resource-cpu${
            isCpuTimeLimitExceeded ? " resource-limit-exceeded" : ""
          }`}
        >
          <span>CPU 시간</span>

          <strong>
            {cpuTimeMs == null ? (
              "측정 전"
            ) : (
              <>
                {(cpuTimeMs / 1000).toFixed(3)}
                {cpuTimeLimitMs ? (
                  <>
                    {" / "}
                    {(cpuTimeLimitMs / 1000).toFixed(0)}
                  </>
                ) : null}
                <small> sec</small>
              </>
            )}
          </strong>

          <div className="resource-progress-row">
            <div className="resource-progress">
              <span
                style={{
                  width: `${cpuTimePercentage}%`,
                }}
              />
            </div>

            {cpuTimeMs != null && <em>{cpuTimePercentage}%</em>}
          </div>
        </article>
      </div>

      <div className="resource-charts">
        <div
          className={`resource-chart-item${
            fullscreenChart === "cpu" ? " resource-chart-fullscreen" : ""
          }`}
        >
          <div className="resource-chart-header">
            <h3>구간별 CPU 사용률 추이</h3>

            <button
              type="button"
              className="chart-fullscreen-button"
              aria-label={
                fullscreenChart === "cpu"
                  ? "CPU 그래프 전체 화면 종료"
                  : "CPU 그래프 전체 화면"
              }
              title={fullscreenChart === "cpu" ? "전체 화면 종료" : "전체 화면"}
              onClick={() =>
                setFullscreenChart((current) =>
                  current === "cpu" ? null : "cpu",
                )
              }
            >
              {fullscreenChart === "cpu" ? "×" : "⛶"}
            </button>
          </div>

          <CpuUsageChart samples={resourceUsage?.cpu_usage_samples} />
        </div>

        <div
          className={`resource-chart-item${
            fullscreenChart === "memory" ? " resource-chart-fullscreen" : ""
          }`}
        >
          <div className="resource-chart-header">
            <h3>구간별 메모리 사용량 추이</h3>

            <button
              type="button"
              className="chart-fullscreen-button"
              aria-label={
                fullscreenChart === "memory"
                  ? "메모리 그래프 전체 화면 종료"
                  : "메모리 그래프 전체 화면"
              }
              title={
                fullscreenChart === "memory" ? "전체 화면 종료" : "전체 화면"
              }
              onClick={() =>
                setFullscreenChart((current) =>
                  current === "memory" ? null : "memory",
                )
              }
            >
              {fullscreenChart === "memory" ? "×" : "⛶"}
            </button>
          </div>

          <MemoryUsageChart samples={resourceUsage?.memory_usage_samples} />
        </div>
      </div>
    </section>
  );
}

export default ResourceUsagePanel;
