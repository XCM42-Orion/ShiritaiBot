import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ApiClient } from "../src/api/client";
import type { RoutineRecord } from "../src/api/types";
import type { Action } from "../src/pages/common";
import { RoutinePage } from "../src/pages/RoutinePage";

afterEach(cleanup);

const today = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
}).format(new Date());

function routine(
  id: string,
  kind: RoutineRecord["kind"],
  day = today,
): RoutineRecord {
  return {
    id,
    kind,
    title: id,
    due: `${day}T09:00:00+08:00`,
    end: null,
    cron: "",
    timezone: "Asia/Shanghai",
    status: "open",
    next_fire: null,
  };
}

function setup(rows: RoutineRecord[]) {
  const client = {
    records: vi.fn(async () => rows),
    createRoutine: vi.fn(async () => rows[0]),
    setRoutineStatus: vi.fn(async () => rows[0]),
  } as unknown as ApiClient;
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  queryClient.setQueryData(["records", "routine"], rows);
  const act: Action = async (work) => {
    await work();
  };
  render(
    <QueryClientProvider client={queryClient}>
      <RoutinePage client={client} act={act} />
    </QueryClientProvider>,
  );
  return { client, user: userEvent.setup() };
}

it("hides courses in month cells and shows all arrangements in the date popover", async () => {
  const { user, client } = setup([
    routine("电动力学", "course"),
    routine("实验课", "course"),
    routine("组会", "event"),
  ]);
  const cell = screen.getByRole("gridcell", { name: `${today}，1 项` });
  expect(within(cell).getByText("组会")).toBeTruthy();
  expect(cell.querySelectorAll(".calendar-dots i")).toHaveLength(1);
  expect(screen.queryByText("电动力学")).toBeNull();
  expect(screen.queryByRole("dialog")).toBeNull();

  await user.click(cell);
  const dialog = screen.getByRole("dialog");
  expect(dialog.className).toContain("routine-day-popover");
  expect(within(dialog).getByText("电动力学")).toBeTruthy();
  expect(within(dialog).getByText("实验课")).toBeTruthy();
  expect(within(dialog).getByText("组会")).toBeTruthy();
  await user.click(within(dialog).getAllByRole("button", { name: "完成" })[0]);
  expect(client.setRoutineStatus).toHaveBeenCalledWith("电动力学", "completed");
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(cell);
});

it("opens course-only days, switches dates, and dismisses from outside or the close button", async () => {
  const { user } = setup([routine("仅课程", "course")]);
  const cell = screen.getByRole("gridcell", { name: `${today}，0 项` });
  expect(cell.querySelector(".calendar-dots")).toBeNull();
  await user.click(cell);
  expect(within(screen.getByRole("dialog")).getByText("仅课程")).toBeTruthy();

  const emptyCell = screen
    .getAllByRole("gridcell")
    .find((item) => item !== cell)!;
  await user.click(emptyCell);
  expect(
    within(screen.getByRole("dialog")).getByText("这一天没有安排。"),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "关闭当日安排" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  await user.click(cell);
  await user.click(screen.getByRole("heading", { name: "安排下一件事" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  await user.click(cell);
  await user.click(cell);
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("includes a course on each day of its span and closes details on month navigation", async () => {
  const next = new Date(`${today}T00:00:00Z`);
  next.setUTCDate(next.getUTCDate() + 1);
  const tomorrow = next.toISOString().slice(0, 10);
  const { user } = setup([
    { ...routine("跨日课程", "course"), end: `${tomorrow}T10:00:00+08:00` },
  ]);
  await user.click(screen.getByRole("gridcell", { name: `${tomorrow}，0 项` }));
  expect(within(screen.getByRole("dialog")).getByText("跨日课程")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "下个月" }));
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("creates courses with a required start time", async () => {
  const { user, client } = setup([]);
  await user.selectOptions(
    screen.getByRole("combobox", { name: "类型" }),
    "course",
  );
  expect((screen.getByLabelText("时间") as HTMLInputElement).required).toBe(
    true,
  );
  await user.type(screen.getByRole("textbox", { name: "内容" }), "量子力学");
  await user.type(screen.getByLabelText("时间"), `${today}T09:00`);
  await user.click(screen.getByRole("button", { name: "创建" }));
  expect(client.createRoutine).toHaveBeenCalledWith({
    kind: "course",
    title: "量子力学",
    due: new Date(`${today}T09:00`).toISOString(),
    cron: "",
    timezone: "Asia/Shanghai",
  });
});
