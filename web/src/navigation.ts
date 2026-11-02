import {
  Activity,
  Bot,
  CalendarDays,
  Database,
  LayoutDashboard,
  MessageSquare,
  Network,
  Plug,
  Puzzle,
  Settings,
  ShieldCheck,
  Smile,
  Sparkles,
  Terminal,
  Workflow,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export type PageId =
  | "overview"
  | "chat"
  | "events"
  | "routine"
  | "memory"
  | "memes"
  | "subagents"
  | "plugins"
  | "skills"
  | "mcp"
  | "models"
  | "adapters"
  | "approvals"
  | "logs"
  | "settings";

export interface PageMeta {
  id: PageId;
  label: string;
  subtitle: string;
  icon: LucideIcon;
}

export const PAGES: PageMeta[] = [
  {
    id: "overview",
    label: "概览",
    subtitle: "运行状态、数据统计与最近动态。",
    icon: LayoutDashboard,
  },
  {
    id: "chat",
    label: "对话",
    subtitle: "直接和助手对话，或输入 / 使用命令。",
    icon: MessageSquare,
  },
  {
    id: "events",
    label: "主动动态",
    subtitle: "主动性引擎收到的事件与分诊结果。",
    icon: Activity,
  },
  {
    id: "routine",
    label: "日程与任务",
    subtitle: "待办、日程、提醒和定时任务。",
    icon: CalendarDays,
  },
  {
    id: "memory",
    label: "长期记忆",
    subtitle: "助手明确记住的长期记忆。",
    icon: Database,
  },
  {
    id: "memes",
    label: "表情管理",
    subtitle: "表情库、分类、自动收录与使用设置。",
    icon: Smile,
  },
  {
    id: "subagents",
    label: "子代理",
    subtitle: "外部 Agent CLI 实例与任务记录。",
    icon: Workflow,
  },
  {
    id: "plugins",
    label: "插件",
    subtitle: "插件启停、配置和重载。",
    icon: Puzzle,
  },
  {
    id: "skills",
    label: "技能",
    subtitle: "安装、查看和停用 .skill 技能包。",
    icon: Sparkles,
  },
  {
    id: "mcp",
    label: "MCP 服务",
    subtitle: "接入 MCP 服务器，把它们的工具提供给助手。",
    icon: Plug,
  },
  {
    id: "models",
    label: "模型与服务",
    subtitle: "LLM 提供商、模型与路由。",
    icon: Bot,
  },
  {
    id: "adapters",
    label: "协议连接",
    subtitle: "OneBot V11 协议连接。",
    icon: Network,
  },
  {
    id: "approvals",
    label: "工具与权限",
    subtitle: "Main 会话模式、逐工具权限、Proactive 默认权限与审批记录。",
    icon: ShieldCheck,
  },
  {
    id: "logs",
    label: "活动日志",
    subtitle: "活动日志，按时间倒序。",
    icon: Terminal,
  },
  {
    id: "settings",
    label: "设置",
    subtitle: "人格、联网搜索与主动性策略。",
    icon: Settings,
  },
];

const PAGE_IDS = new Set<string>(PAGES.map((page) => page.id));

export function pageFromHash(hash: string): PageId {
  const id = hash.replace(/^#\/?/, "").split(/[/?]/)[0];
  return PAGE_IDS.has(id) ? (id as PageId) : "overview";
}

export function hashForPage(id: PageId): string {
  return "#/" + id;
}

export function pageMeta(id: PageId): PageMeta {
  return PAGES.find((page) => page.id === id) ?? PAGES[0];
}
