import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ApiClient } from "../api/client";
import type {
  Currency,
  ModelUsage,
  Statistics,
  TokenBucket,
} from "../api/types";
import { formatHour, formatNumber } from "../lib/utils";

const CURRENCY_SYMBOL: Record<Currency, string> = { USD: "$", CNY: "¥" };

function formatCost(value: number, currency: Currency): string {
  const digits = value > 0 && value < 0.01 ? 4 : 2;
  return CURRENCY_SYMBOL[currency] + value.toFixed(digits);
}

function formatRate(value: number | null): string {
  return value === null ? "—" : (value * 100).toFixed(1) + "%";
}

function totalCost(costs: Statistics["costs"]): string {
  const parts = (Object.keys(CURRENCY_SYMBOL) as Currency[])
    .filter((currency) => costs[currency] !== undefined)
    .map((currency) => formatCost(costs[currency]!, currency));
  return parts.length ? parts.join(" + ") : "—";
}

function ModelTable({ rows }: { rows: ModelUsage[] }) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h3>模型用量</h3>
      </div>
      {rows.length ? (
        <div className="table-scroll">
          <table className="usage-table">
            <thead>
              <tr>
                <th scope="col">模型</th>
                <th scope="col">调用</th>
                <th scope="col">输入 tokens</th>
                <th scope="col">缓存命中</th>
                <th scope="col">命中率</th>
                <th scope="col">输出 tokens</th>
                <th scope="col">预估花费</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.model}>
                  <th scope="row">{row.model || "（未知模型）"}</th>
                  <td>{formatNumber(row.llm_calls)}</td>
                  <td>{formatNumber(row.input_tokens)}</td>
                  <td>{formatNumber(row.cached_tokens)}</td>
                  <td>
                    <span className="rate">
                      <i
                        aria-hidden="true"
                        style={{
                          width: `${(row.cache_hit_rate ?? 0) * 100}%`,
                        }}
                      />
                    </span>
                    {formatRate(row.cache_hit_rate)}
                  </td>
                  <td>{formatNumber(row.output_tokens)}</td>
                  <td>
                    {row.cost !== null && row.currency ? (
                      formatCost(row.cost, row.currency)
                    ) : (
                      <span
                        className="muted"
                        title="在「模型」页填写或获取单价"
                      >
                        未设单价
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">此区间内没有模型调用。</p>
      )}
    </section>
  );
}

const HOUR_RANGES = [
  [24, "24 小时"],
  [72, "3 天"],
  [168, "7 天"],
] as const;

interface Series {
  key: keyof TokenBucket;
  label: string;
  color: string;
}

function HourlyChart({
  title,
  rows,
  series,
}: {
  title: string;
  rows: TokenBucket[];
  series: Series[];
}) {
  const [hover, setHover] = useState<number | null>(null);
  const maximum = Math.max(
    1,
    ...rows.map((row) =>
      series.reduce((sum, item) => sum + Number(row[item.key]), 0),
    ),
  );
  const width = 900;
  const height = 190;
  const step = width / Math.max(1, rows.length);
  const selected = hover === null ? null : rows[hover];

  return (
    <section className="panel chart-panel">
      <div className="panel-head">
        <h3>{title}</h3>
        <div className="chart-legend">
          {series.map((item) => (
            <span key={item.key}>
              <i style={{ background: item.color }} />
              {item.label}
            </span>
          ))}
        </div>
      </div>
      <div className="chart-scale" aria-hidden="true">
        <span>{formatNumber(maximum)}</span>
        <span>0</span>
      </div>
      <svg
        className="hourly-chart"
        viewBox={`0 0 ${width} ${height + 28}`}
        role="group"
        aria-label={title + "逐小时图表"}
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
              aria-label={`${formatHour(row.hour)} ${series
                .map((item) => `${item.label} ${row[item.key]}`)
                .join("，")}`}
              onMouseEnter={() => setHover(index)}
              onFocus={() => setHover(index)}
              onBlur={() => setHover(null)}
            >
              <rect
                x={index * step}
                y={0}
                width={step}
                height={height}
                fill={index === hover ? "var(--accent-soft)" : "transparent"}
              />
              {series.map((item) => {
                const value = (Number(row[item.key]) / maximum) * height;
                offset += value;
                return (
                  <rect
                    key={item.key}
                    x={index * step + step * 0.15}
                    y={height - offset}
                    width={step * 0.7}
                    height={value}
                    rx={1}
                    fill={item.color}
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
                  {formatHour(row.hour)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <div className="chart-detail" aria-live="polite">
        {selected ? (
          <>
            <strong>{formatHour(selected.hour)} 时</strong>
            {series.map((item) => (
              <span key={item.key}>
                {item.label} {formatNumber(Number(selected[item.key]))}
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

export function Dashboard({ client }: { client: ApiClient }) {
  const [hours, setHours] = useState<number>(24);
  const query = useQuery({
    queryKey: ["stats", hours],
    queryFn: () => client.stats(hours),
    refetchInterval: 30000,
  });
  const data: Statistics | undefined = query.data;

  return (
    <section className="dashboard">
      <div className="panel-head">
        <div>
          <h2>数据看板</h2>
          <p className="muted">北京时间 · 每小时聚合 · 每 30 秒更新</p>
        </div>
        <div className="range-tabs" role="group" aria-label="统计区间">
          {HOUR_RANGES.map(([value, label]) => (
            <button
              type="button"
              key={value}
              className={hours === value ? "selected" : ""}
              aria-pressed={hours === value}
              onClick={() => setHours(value)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {query.error && (
        <QueryErrorNotice message={`读取统计失败：${query.error.message}`} />
      )}
      {data && data.totals && data.buckets ? (
        <>
          <div className="metrics metrics-wide">
            {(
              [
                ["总 tokens", formatNumber(data.totals.total_tokens)],
                [
                  "缓存命中率",
                  formatRate(
                    data.totals.input_tokens
                      ? data.totals.cached_tokens / data.totals.input_tokens
                      : null,
                  ),
                ],
                ["预估花费", totalCost(data.costs ?? {})],
                ["对话消息", formatNumber(data.totals.messages)],
                ["模型调用", formatNumber(data.totals.llm_calls)],
                ["失败轮次", formatNumber(data.totals.failed_turns)],
              ] as const
            ).map(([label, value]) => (
              <div className="metric" key={label}>
                <span>{label}</span>
                <strong>{value}</strong>
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
              {
                key: "input_tokens",
                label: "输入",
                color: "var(--chart-input)",
              },
              {
                key: "output_tokens",
                label: "输出",
                color: "var(--chart-output)",
              },
            ]}
          />
          <ModelTable rows={data.models ?? []} />
          <HourlyChart
            title="对话消息"
            rows={data.buckets}
            series={[
              {
                key: "incoming_messages",
                label: "用户消息",
                color: "var(--chart-incoming)",
              },
              {
                key: "outgoing_messages",
                label: "助手回复",
                color: "var(--chart-outgoing)",
              },
            ]}
          />
          <p className="muted stats-note">
            统计区间 {formatHour(data.start)} 至 {formatHour(data.end)}
            ，首尾小时按实际区间截取。Token
            取提供商返回的用量，包含工具轮次与连接测试； 缓存命中{" "}
            {formatNumber(data.totals.cached_tokens)} tokens（已计入输入）。
            花费按模型当前单价估算，仅供参考：缓存写入按输入单价计，不含分级定价与免费额度；
            已删除或未设单价的模型不计入。
            消息统计已入库的用户消息和最终回复，包含斜杠命令，不含工具结果与内部交接；
            未返回用量的请求不估算 tokens。
          </p>
        </>
      ) : (
        !query.error && <p className="muted">正在读取统计…</p>
      )}
    </section>
  );
}
