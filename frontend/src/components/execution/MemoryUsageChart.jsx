function MemoryUsageChart({ samples }) {
  const points = (samples ?? [])
    .filter(
      (sample) =>
        Number.isFinite(sample.elapsed_ms) &&
        Number.isFinite(sample.memory_bytes) &&
        sample.elapsed_ms >= 0 &&
        sample.memory_bytes >= 0,
    )
    .map((sample) => ({
      time: sample.elapsed_ms,
      usage: sample.memory_bytes / (1024 * 1024),
    }))
    .sort((a, b) => a.time - b.time);

  if (points.length === 0) {
    return <p className="cpu-chart-empty">메모리 사용량 데이터가 없습니다.</p>;
  }

  const width = 720;
  const height = 230;
  const left = 90;
  const right = 16;
  const top = 16;
  const bottom = 36;

  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;

  const maxTime = Math.max(...points.map((p) => p.time), 1);
  const maxMemory = Math.max(
    16,
    Math.ceil(Math.max(...points.map((p) => p.usage)) / 16) * 16,
  );

  const x = (time) => left + (time / maxTime) * plotWidth;
  const y = (usage) => top + plotHeight - (usage / maxMemory) * plotHeight;

  const linePoints = points.map((p) => `${x(p.time)},${y(p.usage)}`).join(" ");

  const gridValues = [0, 0.25, 0.5, 0.75, 1];

  return (
    <div className="cpu-chart-scroll">
      <svg
        className="cpu-chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="실행 경과 시간에 따른 메모리 사용량"
      >
        {/* 가로 격자 및 메모리 사용량 눈금 */}
        {gridValues.map((ratio) => {
          const value = maxMemory * ratio;
          const posY = y(value);

          return (
            <g key={ratio}>
              <line
                x1={left}
                y1={posY}
                x2={width - right}
                y2={posY}
                className="cpu-chart-grid"
              />
              <text
                x={left - 10}
                y={posY + 4}
                textAnchor="end"
                className="cpu-chart-label"
              >
                {Math.round(value)} MB
              </text>
            </g>
          );
        })}

        {/* 시간축 */}
        {[0, 0.5, 1].map((ratio) => (
          <text
            key={ratio}
            x={x(maxTime * ratio)}
            y={height - 12}
            textAnchor={ratio === 0 ? "start" : ratio === 1 ? "end" : "middle"}
            className="cpu-chart-label"
          >
            {((maxTime * ratio) / 1000).toFixed(2)}s
          </text>
        ))}

        {/* 메모리 사용량 변화 선 */}
        <polyline
          points={linePoints}
          className="cpu-chart-line memory-chart-line"
        />

        {/* 측정 지점: 마우스를 올리면 수치 표시 */}
        {points.map((point, index) => (
          <circle
            key={`${point.time}-${index}`}
            cx={x(point.time)}
            cy={y(point.usage)}
            r="3"
            className="cpu-chart-point memory-chart-point"
          >
            <title>
              {`${(point.time / 1000).toFixed(3)}초: ${point.usage.toFixed(2)} MB`}
            </title>
          </circle>
        ))}
      </svg>
    </div>
  );
}

export default MemoryUsageChart;
