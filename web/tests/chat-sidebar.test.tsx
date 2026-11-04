import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatSidebar } from "../src/components/ChatSidebar";
import { sessionGroups } from "../src/lib/sessions";

afterEach(cleanup);

const groups = sessionGroups(
  ["web:owner", "private:qq:2064438401", "group:qq:820215017"],
  {},
);

describe("ChatSidebar", () => {
  it("renders groups, markers and reports selection", async () => {
    const onSelect = vi.fn();
    const onNew = vi.fn();
    render(
      <ChatSidebar
        groups={groups}
        activity={{}}
        selected="web:owner"
        running={new Set(["private:qq:2064438401"])}
        pending={new Set(["group:qq:820215017"])}
        onSelect={onSelect}
        onNew={onNew}
      />,
    );
    expect(screen.getByText("本机")).toBeTruthy();
    expect(screen.getByText("QQ 私聊")).toBeTruthy();
    expect(screen.queryByText("后台任务")).toBeNull();
    const active = screen.getByRole("button", { name: /默认对话/ });
    expect(active.getAttribute("aria-current")).toBe("page");
    expect(
      screen
        .getByRole("button", { name: /QQ 2064438401/ })
        .querySelector(".session-dot"),
    ).toBeTruthy();
    expect(
      screen
        .getByRole("button", { name: /群 820215017/ })
        .querySelector(".session-badge"),
    ).toBeTruthy();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /群 820215017/ }));
    expect(onSelect).toHaveBeenCalledWith("group:qq:820215017");
    await user.click(screen.getByRole("button", { name: /新对话/ }));
    expect(onNew).toHaveBeenCalled();
  });

  it("shows group counts", () => {
    render(
      <ChatSidebar
        groups={sessionGroups(["web:owner", "web:1762321920000"], {})}
        activity={{}}
        selected="web:owner"
        running={new Set()}
        pending={new Set()}
        onSelect={() => {}}
        onNew={() => {}}
      />,
    );
    expect(screen.getByText("2")).toBeTruthy();
  });
});
