import type { TicketPriority } from "./types";

export const DEMO_EXAMPLES = [
  {
    id: "knowledge",
    number: "01",
    title: "问知识",
    prompt: "公司 VPN 连不上时应该先检查什么？",
    expected: "看到操作建议，以及与《VPN 连接故障处理》相关的检索片段。",
  },
  {
    id: "ticket",
    number: "02",
    title: "查本人工单",
    prompt: "查询工单 IT-2026-0001 的进度",
    expected: "看到工单状态；演示用户 u-001 可以查看这张工单。",
  },
  {
    id: "create",
    number: "03",
    title: "确认后建单",
    prompt: "VPN 一直连接失败，请创建工单",
    expected: "先出现草稿，核对并确认后才会写入工单。",
  },
  {
    id: "handoff",
    number: "04",
    title: "验证安全兜底",
    prompt: "我桌上的未知型号量子终端出现紫色告警，怎么修？",
    expected: "资料不足时转人工，不编造维修步骤。",
  },
] as const;

export const PRIORITY_INFO: { value: TicketPriority; label: string; description: string }[] = [
  { value: "low", label: "低", description: "影响较小，工作仍可继续" },
  { value: "medium", label: "中", description: "影响个人日常工作" },
  { value: "high", label: "高", description: "关键工作受到阻碍" },
  { value: "critical", label: "紧急", description: "大范围中断或重大风险" },
];

export const PRIORITY_LABELS: Record<TicketPriority, string> = Object.fromEntries(
  PRIORITY_INFO.map(({ value, label }) => [value, label]),
) as Record<TicketPriority, string>;

export const STATUS_LABELS: Record<string, string> = {
  pending: "待分派",
  in_progress: "处理中",
  resolved: "已解决，待确认",
  closed: "已关闭",
};
