import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

type Row = { hour: string; [key: string]: string | number };
const number = (value: number) => value.toLocaleString("zh-CN");
const time = (value: string) =>
  new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    hour12: false,
  }).format(new Date(value));

function HourlyChart({
  title,
  rows,
  series,
}: {
  title: string;
  rows: Row[];
  series: { key: string; label: string; color: string }[];
}) {
  const [hover, setHover] = useState<number | null>(null);
  const maximum = Math.max(
    1,
    ...rows.map((row) =>
      series.reduce((sum, s) => sum + Number(row[s.key]), 0),
    ),
  );
  const width = 900,
    height = 190,
    step = width / Math.max(1, rows.length);
  const selected = hover === null ? null : rows[hover];
  return (
    <section className="panel chart-panel">
      <div className="panel-head">
        <h3>{title}</h3>
        <div className="chart-legend">
          {series.map((s) => (
            <span key={s.key}>
              <i style={{ background: s.color }} />
              {s.label}
            </span>
          ))}
        </div>
      </div>
      <div className="chart-scale">
        <span>{number(maximum)}</span>
        <span>0</span>
      </div>
      <svg
        className="hourly-chart"
        viewBox={`0 0 ${width} ${height + 28}`}
        role="group"
        aria-label={`${title}逐小时图表`}
        onMouseLeave={() => setHover(null)}
      >
        {[0, 0.5, 1].map((level) => (
          <line
            key={level}
            x1={0}
            y1={level * height}
            x2={width}
            y2={level * height}
            stroke="currentColor"
            opacity=".12"
          />
        ))}
        {rows.map((row, index) => {
          let offset = 0;
          return (
            <g
              key={row.hour}
              tabIndex={0}
              role="img"
              aria-label={`${time(row.hour)} ${series.map((s) => `${s.label} ${row[s.key]}`).join("，")}`}
              onMouseEnter={() => setHover(index)}
              onFocus={() => setHover(index)}
              onBlur={() => setHover(null)}
            >
              <rect
                x={index * step}
                y={0}
                width={step}
                height={height}
                fill={index === hover ? "#ffffff14" : "transparent"}
              />
              {series.map((s) => {
                const h = (Number(row[s.key]) / maximum) * height;
                offset += h;
                return (
                  <rect
                    key={s.key}
                    x={index * step + step * 0.15}
                    y={height - offset}
                    width={step * 0.7}
                    height={h}
                    rx={1}
                    fill={s.color}
                  />
                );
              })}
              {(index === 0 ||
                index === rows.length - 1 ||
                index % Math.ceil(rows.length / 5) === 0) && (
                <text
                  x={index * step + step / 2}
                  y={height + 22}
                  textAnchor={
                    index === 0
                      ? "start"
                      : index === rows.length - 1
                        ? "end"
                        : "middle"
                  }
                  fill="currentColor"
                  fontSize={12}
                >
                  {time(row.hour)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <div className="chart-detail" aria-live="polite">
        {selected ? (
          <>
            <strong>{time(selected.hour)} 时</strong>
            {series.map((s) => (
              <span key={s.key}>
                {s.label} {number(Number(selected[s.key]))}
              </span>
            ))}
          </>
        ) : (
          <span className="muted">悬停或用 Tab 选中柱形，查看该小时数据。</span>
        )}
      </div>
    </section>
  );
}

export function Dashboard({
  api,
  onError,
}: {
  api: (path: string) => Promise<any>;
  onError: (error: Error) => void;
}) {
  const [hours, setHours] = useState(24);
  const query = useQuery({
    queryKey: ["stats", hours],
    queryFn: () => api(`/stats?hours=${hours}`),
    refetchInterval: 30000,
  });
  useEffect(() => {
    if (query.error) onError(query.error);
  }, [query.error]);
  const data = query.data;
  return (
    <section className="dashboard">
      <div className="panel-head">
        <div>
          <h2>数据看板</h2>
          <p className="muted">北京时间 · 每小时聚合 · 每 30 秒更新</p>
        </div>
        <div className="range-tabs">
          {[
            [24, "24 小时"],
            [72, "3 天"],
            [168, "7 天"],
          ].map(([value, label]) => (
            <button
              key={value}
              className={hours === value ? "selected" : ""}
              onClick={() => setHours(Number(value))}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {data ? (
        <>
          <div className="metrics">
            {[
              ["总 tokens", "total_tokens"],
              ["对话消息", "messages"],
              ["模型调用", "llm_calls"],
              ["失败轮次", "failed_turns"],
            ].map(([label, key]) => (
              <div className="metric" key={key}>
                <span>{label}</span>
                <strong>{number(data.totals[key])}</strong>
                <small>
                  过去 {hours === 24 ? "24 小时" : `${hours / 24} 天`}
                </small>
              </div>
            ))}
          </div>
          <HourlyChart
            title="Token 用量"
            rows={data.buckets}
            series={[
              { key: "input_tokens", label: "输入", color: "#8dcfb3" },
              { key: "output_tokens", label: "输出", color: "#8d9ce0" },
            ]}
          />
          <HourlyChart
            title="对话消息"
            rows={data.buckets}
            series={[
              { key: "incoming_messages", label: "用户消息", color: "#8dcfb3" },
              { key: "outgoing_messages", label: "助手回复", color: "#e2bb82" },
            ]}
          />
          <p className="muted stats-note">
            统计区间 {time(data.start)} 至 {time(data.end)}
            ，首尾小时按实际区间截取。Token
            取提供商返回的用量，包含工具轮次与连接测试；缓存命中{" "}
            {number(data.totals.cached_tokens)}{" "}
            tokens（已计入输入）。消息统计已入库的用户消息和最终回复，包含斜杠命令，不含工具结果与内部交接；未返回用量的请求不估算
            tokens。
          </p>
        </>
      ) : (
        <p className="muted">
          {query.isPending ? "正在读取统计…" : "暂时无法读取统计"}
        </p>
      )}
    </section>
  );
}
