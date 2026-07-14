"use client";

import { FormEvent, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  Clock3,
  Loader2,
  RotateCcw,
  Send,
  Square,
  Wrench,
  XCircle,
} from "lucide-react";
import type {
  AgentEvent,
  AgentPhase,
  AgentRun,
  AgentTraceResponse,
} from "@/lib/api";

const PHASES: Array<{ id: AgentPhase; label: string }> = [
  { id: "plan", label: "规划" },
  { id: "discover", label: "检索" },
  { id: "extract", label: "抽取" },
  { id: "analyze", label: "分析" },
  { id: "synthesize", label: "综合" },
  { id: "verify", label: "验证" },
  { id: "finalize", label: "封装" },
];

interface Props {
  run: AgentRun;
  events: AgentEvent[];
  trace?: AgentTraceResponse | null;
  onSubmitInput?: (content: string) => Promise<void>;
  onCancel?: () => Promise<void>;
  onRetry?: () => Promise<void>;
}

export default function AgentTrace({
  run,
  events,
  trace,
  onSubmitInput,
  onCancel,
  onRetry,
}: Props) {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState<"input" | "cancel" | "retry" | null>(null);
  const currentIndex = PHASES.findIndex((phase) => phase.id === run.current_phase);
  const recentEvents = useMemo(() => events.slice(-8).reverse(), [events]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const value = input.trim();
    if (!value || !onSubmitInput) return;
    setBusy("input");
    try {
      await onSubmitInput(value);
      setInput("");
    } finally {
      setBusy(null);
    }
  };

  const runAction = async (kind: "cancel" | "retry", action?: () => Promise<void>) => {
    if (!action) return;
    setBusy(kind);
    try {
      await action();
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="overflow-hidden rounded-lg border border-border bg-bg-surface">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-border px-5 py-4">
        <div>
          <div className="eyebrow">agent trace</div>
          <h2 className="mt-1 text-17 font-medium text-text-primary">执行轨迹</h2>
          <p className="mt-1 text-12 text-text-muted">{run.progress}</p>
        </div>
        <div className="flex items-center gap-2">
          {(run.status === "queued" || run.status === "running" || run.status === "waiting_input") &&
            onCancel && (
              <button
                type="button"
                onClick={() => runAction("cancel", onCancel)}
                disabled={busy !== null}
                className="inline-flex h-8 items-center gap-2 rounded-sm border border-border px-3 text-11 text-text-secondary transition-colors hover:border-border-strong hover:text-text-primary disabled:opacity-50"
              >
                {busy === "cancel" ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Square className="h-3.5 w-3.5" />
                )}
                取消
              </button>
            )}
          {run.status === "failed" && onRetry && (
            <button
              type="button"
              onClick={() => runAction("retry", onRetry)}
              disabled={busy !== null}
              className="inline-flex h-8 items-center gap-2 rounded-sm bg-[#B4AEFF] px-3 text-11 font-medium text-[#111321] disabled:opacity-50"
            >
              {busy === "retry" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RotateCcw className="h-3.5 w-3.5" />
              )}
              重试
            </button>
          )}
        </div>
      </header>

      <div className="grid gap-0 lg:grid-cols-[220px_minmax(0,1fr)]">
        <ol className="border-b border-border p-3 lg:border-b-0 lg:border-r">
          {PHASES.map((phase, index) => {
            const state = phaseState(run, index, currentIndex);
            return (
              <li key={phase.id} className="flex min-h-10 items-center gap-3 px-2 py-1.5">
                <PhaseIcon state={state} />
                <div className="min-w-0 flex-1">
                  <div className="text-12 text-text-primary">{phase.label}</div>
                  <div className="num mt-0.5 text-9 uppercase text-text-muted">{phase.id}</div>
                </div>
              </li>
            );
          })}
        </ol>

        <div className="min-w-0 p-5">
          {run.status === "waiting_input" && run.pending_question && onSubmitInput && (
            <form onSubmit={submit} className="mb-5 border-b border-border pb-5">
              <label htmlFor="agent-input" className="text-12 font-medium text-text-primary">
                {run.pending_question}
              </label>
              <div className="mt-3 flex gap-2">
                <input
                  id="agent-input"
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  className="h-9 min-w-0 flex-1 rounded-sm border border-border bg-bg-inset px-3 text-12 text-text-primary outline-none focus:border-[#B4AEFF]"
                  maxLength={1000}
                />
                <button
                  type="submit"
                  disabled={!input.trim() || busy !== null}
                  title="提交补充信息"
                  className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-sm bg-[#B4AEFF] text-[#111321] disabled:opacity-40"
                >
                  {busy === "input" ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Send className="h-4 w-4" />
                  )}
                </button>
              </div>
            </form>
          )}

          {(run.status === "failed" || run.status === "cancelled") && (
            <div className="mb-5 flex gap-3 border-b border-border pb-5 text-12">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[#FFB547]" />
              <div>
                <div className="text-text-primary">
                  {run.status === "cancelled" ? "任务已取消" : run.error_message || "任务失败"}
                </div>
                {run.error_code && <div className="num mt-1 text-10 text-text-muted">{run.error_code}</div>}
              </div>
            </div>
          )}

          <div className="grid gap-5 xl:grid-cols-2">
            <div>
              <h3 className="text-11 font-medium uppercase text-text-muted">最近事件</h3>
              <div className="mt-3 space-y-3">
                {recentEvents.length === 0 ? (
                  <p className="text-12 text-text-muted">等待首个事件</p>
                ) : (
                  recentEvents.map((event) => (
                    <div key={event.sequence} className="flex gap-3">
                      <Clock3 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-text-muted" />
                      <div className="min-w-0">
                        <div className="text-12 text-text-secondary">{event.message}</div>
                        <div className="num mt-1 text-9 text-text-muted">
                          {event.phase || "run"} · #{event.sequence}
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>

            <div>
              <h3 className="text-11 font-medium uppercase text-text-muted">工具调用</h3>
              <div className="mt-3 space-y-3">
                {!trace || trace.tool_calls.length === 0 ? (
                  <p className="text-12 text-text-muted">暂无工具调用</p>
                ) : (
                  trace.tool_calls.slice(-8).reverse().map((call) => (
                    <div key={call.id} className="flex gap-3">
                      <Wrench className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#7DD3FC]" />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-3">
                          <span className="num truncate text-11 text-text-primary">{call.tool_name}</span>
                          <span className="num shrink-0 text-9 text-text-muted">
                            {call.duration_ms}ms
                          </span>
                        </div>
                        <p className="mt-1 truncate text-10 text-text-muted">
                          {call.result_summary || call.status}
                        </p>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function phaseState(run: AgentRun, index: number, currentIndex: number) {
  if (run.status === "completed") return "completed" as const;
  if (index < currentIndex) return "completed" as const;
  if (index > currentIndex) return "pending" as const;
  if (run.status === "failed") return "failed" as const;
  if (run.status === "cancelled") return "cancelled" as const;
  if (run.status === "waiting_input") return "waiting" as const;
  return "active" as const;
}

function PhaseIcon({
  state,
}: {
  state: "completed" | "pending" | "failed" | "cancelled" | "waiting" | "active";
}) {
  if (state === "completed") return <CheckCircle2 className="h-4 w-4 text-[#4ADE80]" />;
  if (state === "failed") return <XCircle className="h-4 w-4 text-[#FF6B7A]" />;
  if (state === "cancelled") return <Square className="h-4 w-4 text-text-muted" />;
  if (state === "waiting") return <Clock3 className="h-4 w-4 text-[#FFB547]" />;
  if (state === "active") return <Loader2 className="h-4 w-4 animate-spin text-[#B4AEFF]" />;
  return <Circle className="h-4 w-4 text-text-muted" />;
}

