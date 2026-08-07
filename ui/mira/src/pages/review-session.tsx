import {
  AlertCircle,
  ArrowDown,
  Bot,
  Brain,
  Check,
  Circle,
  CircleDot,
  ExternalLink,
  FileCode2,
  LoaderCircle,
  MessageSquare,
  PauseCircle,
  RefreshCw,
  SearchCode,
  ShieldCheck,
  Terminal,
  Wrench,
} from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useParams } from "react-router"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { API_BASE, fetchJson, postJson } from "@/lib/api/http"
import type { ReviewTraceMetrics } from "@/lib/api/types"
import { useDocumentTitle } from "@/lib/hooks"
import { cn } from "@/lib/utils"

type TraceEvent = {
  id: number
  kind: string
  title: string
  detail: string
  data: Record<string, unknown>
  created_at: number
}
type TraceMetrics = ReviewTraceMetrics
type ReviewSession = {
  id: string
  status: "queued" | "running" | "completed" | "failed" | "interrupted"
  owner: string
  repo: string
  pr_number: number
  pr_title: string
  pr_url: string
  head_sha: string
  started_at: number
  finished_at: number | null
  events: TraceEvent[]
  attempt: number
  retry_of: string | null
  replacement_id: string | null
  trace_metrics: TraceMetrics
  recovery_reason?: string
  error?: string
}
type AgentGroup = {
  key: string
  pass: string
  agentId: number
  events: TraceEvent[]
  complete: boolean
  startedAt: number
  finishedAt: number | null
  findings: number
}
type TraceView = "agents" | "milestones" | "pi" | "all"
type EventPresentation = {
  icon: typeof Brain
  className: string
  label: string
}

const eventPresentation: Record<string, EventPresentation> = {
  context: {
    icon: FileCode2,
    className: "bg-sky-500/10 text-sky-700 dark:text-sky-400",
    label: "Context",
  },
  reasoning: {
    icon: Brain,
    className: "bg-violet-500/10 text-violet-700 dark:text-violet-400",
    label: "Analysis",
  },
  output: {
    icon: MessageSquare,
    className: "bg-indigo-500/10 text-indigo-700 dark:text-indigo-400",
    label: "Agent output",
  },
  agent_start: {
    icon: Bot,
    className: "bg-sky-500/10 text-sky-700 dark:text-sky-400",
    label: "Pi agent",
  },
  tool_call: {
    icon: Wrench,
    className: "bg-amber-500/10 text-amber-700 dark:text-amber-400",
    label: "Tool call",
  },
  tool_result: {
    icon: Terminal,
    className: "bg-cyan-500/10 text-cyan-700 dark:text-cyan-400",
    label: "Tool result",
  },
  result: {
    icon: Check,
    className: "bg-teal-500/10 text-teal-700 dark:text-teal-400",
    label: "Model result",
  },
  agent_end: {
    icon: Check,
    className: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
    label: "Pi complete",
  },
  stream: {
    icon: CircleDot,
    className: "bg-muted text-muted-foreground",
    label: "Stream",
  },
  pipeline: {
    icon: ShieldCheck,
    className: "bg-teal-500/10 text-teal-700 dark:text-teal-400",
    label: "Pipeline",
  },
  action: {
    icon: Wrench,
    className: "bg-amber-500/10 text-amber-700 dark:text-amber-400",
    label: "Action",
  },
  decision: {
    icon: Check,
    className: "bg-teal-500/10 text-teal-700 dark:text-teal-400",
    label: "Decision",
  },
  finding: {
    icon: SearchCode,
    className: "bg-orange-500/10 text-orange-700 dark:text-orange-400",
    label: "Finding",
  },
  complete: {
    icon: Check,
    className: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
    label: "Complete",
  },
  error: {
    icon: AlertCircle,
    className: "bg-destructive/10 text-destructive",
    label: "Error",
  },
}

const agentDescriptions: Record<string, string> = {
  walkthrough: "Maps the change and prepares the pull request walkthrough.",
  review: "Checks correctness, regressions, and maintainability.",
  "security review": "Looks for security and trust-boundary issues.",
  "quality critique": "Challenges candidate findings before publication.",
}

function getEventPresentation(kind: unknown): EventPresentation {
  if (typeof kind === "string" && Object.hasOwn(eventPresentation, kind)) {
    return eventPresentation[kind]
  }
  return eventPresentation.action
}

function titleCase(value: string) {
  return value.replace(/(^|\s)\S/g, (letter) => letter.toUpperCase())
}

function agentName(pass: string) {
  if (pass === "review") return "Code review"
  return titleCase(pass)
}

function relativeTime(timestamp: number) {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - timestamp))
  if (seconds < 5) return "now"
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  return `${Math.floor(seconds / 3600)}h ago`
}

function formatDuration(start: number, end: number) {
  const seconds = Math.max(0, Math.round(end - start))
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}

function formatMilliseconds(value: number) {
  const seconds = Math.max(0, Math.round(value / 1000))
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}

function formatCompactNumber(value: number) {
  return new Intl.NumberFormat(undefined, { notation: "compact" }).format(value)
}

function formatCharacters(value: number) {
  if (value < 1000) return `${value} chars`
  return `${formatCompactNumber(value)} chars`
}

function traceMetricsFromEvents(events: TraceEvent[]): TraceMetrics {
  const metrics: TraceMetrics = {
    pi_events: 0,
    llm_calls: 0,
    tool_calls: 0,
    tool_errors: 0,
    reasoning_chars: 0,
    output_chars: 0,
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    total_tokens: 0,
    duration_ms: 0,
    models: [],
    result_tools: [],
  }
  const models = new Set<string>()
  const resultTools = new Set<string>()
  for (const event of events) {
    if (event.data.source !== "pi") continue
    metrics.pi_events += 1
    if (event.kind === "agent_start") metrics.llm_calls += 1
    if (event.kind === "tool_call") metrics.tool_calls += 1
    if (event.kind === "tool_result" && event.data.is_error === true) {
      metrics.tool_errors += 1
    }
    if (event.kind === "reasoning")
      metrics.reasoning_chars += event.detail.length
    if (event.kind === "output") metrics.output_chars += event.detail.length
    if (typeof event.data.model === "string") models.add(event.data.model)
    if (typeof event.data.result_tool === "string") {
      resultTools.add(event.data.result_tool)
    }
    if (event.kind !== "agent_end") continue
    const usage = event.data.usage
    if (!usage || typeof usage !== "object" || Array.isArray(usage)) continue
    const typedUsage = usage as Record<string, unknown>
    if (typeof typedUsage.input === "number")
      metrics.input_tokens += typedUsage.input
    if (typeof typedUsage.output === "number")
      metrics.output_tokens += typedUsage.output
    if (typeof typedUsage.cacheRead === "number") {
      metrics.cache_read_tokens += typedUsage.cacheRead
    }
    if (typeof typedUsage.cacheWrite === "number") {
      metrics.cache_write_tokens += typedUsage.cacheWrite
    }
    if (typeof typedUsage.total === "number")
      metrics.total_tokens += typedUsage.total
    if (typeof event.data.duration_ms === "number") {
      metrics.duration_ms += event.data.duration_ms
    }
  }
  metrics.models = [...models]
  metrics.result_tools = [...resultTools]
  return metrics
}

function mergeReasoning(events: TraceEvent[]) {
  const merged: TraceEvent[] = []
  for (const event of events) {
    const previous = merged.at(-1)
    const sameStream =
      previous &&
      previous.kind === event.kind &&
      (event.kind === "reasoning" || event.kind === "output") &&
      previous.data.run_id === event.data.run_id
    if (previous && sameStream) {
      merged[merged.length - 1] = {
        ...previous,
        id: event.id,
        created_at: event.created_at,
        title: event.kind === "reasoning" ? "Analysis update" : "Agent output",
        detail: `${previous.detail}${event.detail}`,
        data: {
          ...previous.data,
          characters:
            (typeof previous.data.characters === "number"
              ? previous.data.characters
              : previous.detail.length) +
            (typeof event.data.characters === "number"
              ? event.data.characters
              : event.detail.length),
        },
      }
    } else {
      merged.push(event)
    }
  }
  return merged
}

function cleanedEventTitle(event: TraceEvent) {
  const pass = typeof event.data.pass === "string" ? event.data.pass : ""
  const agentId =
    typeof event.data.agent_id === "number" ? event.data.agent_id : null
  if (!pass || agentId === null) return event.title
  const prefix = new RegExp(`^Pi ${pass} agent ${agentId}(?:: )?`, "i")
  const cleaned = event.title.replace(prefix, "")
  if (event.kind === "reasoning") return "Model analysis"
  if (event.kind === "output") return "Model response"
  if (/^complete$/i.test(cleaned)) return `${agentName(pass)} complete`
  if (/^started$/i.test(cleaned) || /^Starting /i.test(event.title)) {
    return `${agentName(pass)} started`
  }
  return cleaned || event.title
}

const hiddenEventData = new Set([
  "source",
  "event_type",
  "pass",
  "agent_id",
  "run_id",
  "characters",
  "channel",
  "boundary",
  "result",
])

const eventDataFields: Record<string, Set<string>> = {
  reasoning: new Set(),
  output: new Set(),
  stream: new Set(),
  agent_start: new Set(["model", "thinking_level", "result_tool"]),
  tool_call: new Set(),
  tool_result: new Set(["is_error"]),
  result: new Set(["model", "result_tool", "usage", "duration_ms"]),
  agent_end: new Set(["model", "usage", "duration_ms"]),
  error: new Set(["model", "result_tool"]),
}

function formatEventValue(value: unknown) {
  if (Array.isArray(value) && value.every((item) => typeof item !== "object")) {
    return value.join(", ")
  }
  if (typeof value === "object" && value !== null) {
    return JSON.stringify(value, null, 2)
  }
  return String(value)
}

function EventRow({
  event,
  isLast,
  showAgent = false,
  streaming = false,
}: {
  event: TraceEvent
  isLast: boolean
  showAgent?: boolean
  streaming?: boolean
}) {
  const presentation = getEventPresentation(event.kind)
  const Icon = presentation.icon
  const pass = typeof event.data.pass === "string" ? event.data.pass : ""
  const agentId =
    typeof event.data.agent_id === "number" ? event.data.agent_id : null
  const visibleFields = eventDataFields[event.kind]
  const detailData = Object.entries(event.data || {}).filter(
    ([key, value]) =>
      (visibleFields ? visibleFields.has(key) : !hiddenEventData.has(key)) &&
      !(key === "is_error" && value === false)
  )
  const isModelStream = event.kind === "reasoning" || event.kind === "output"
  const isActivelyStreaming = streaming && isModelStream

  return (
    <li className="relative flex gap-4 pb-6">
      {!isLast && (
        <span
          aria-hidden
          className="absolute top-9 left-4 h-[calc(100%-1.25rem)] w-px bg-border"
        />
      )}
      <span
        className={cn(
          "relative z-10 flex size-8 shrink-0 items-center justify-center rounded-full",
          presentation.className
        )}
      >
        <Icon className="size-4" aria-hidden />
      </span>
      <article className="min-w-0 flex-1 pt-0.5">
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-medium">
                {cleanedEventTitle(event)}
              </h3>
              <Badge variant="outline" className="text-[10px] font-normal">
                {presentation.label}
              </Badge>
              {showAgent && pass && agentId !== null && (
                <Badge variant="secondary" className="text-[10px] font-normal">
                  {agentName(pass)}
                </Badge>
              )}
            </div>
          </div>
          <time
            className="shrink-0 text-xs text-muted-foreground"
            dateTime={new Date(event.created_at * 1000).toISOString()}
          >
            {relativeTime(event.created_at)}
          </time>
        </div>

        {event.detail &&
          (isModelStream ? (
            <div
              className={cn(
                "mt-3 rounded-lg border bg-muted/25 px-4 py-3",
                event.kind === "reasoning" &&
                  "border-violet-500/20 bg-violet-500/5",
                event.kind === "output" &&
                  "border-indigo-500/20 bg-indigo-500/5"
              )}
            >
              <div className="mb-2 flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <span
                  className={cn(
                    "size-1.5 rounded-full",
                    event.kind === "reasoning"
                      ? "bg-violet-500"
                      : "bg-indigo-500",
                    isActivelyStreaming &&
                      "animate-pulse motion-reduce:animate-none"
                  )}
                  aria-hidden
                />
                <span>
                  {event.kind === "reasoning"
                    ? "Model analysis"
                    : "Model response"}
                </span>
                {isActivelyStreaming && (
                  <span className="font-normal">· streaming</span>
                )}
              </div>
              <p className="max-w-[75ch] text-sm leading-6 break-words whitespace-pre-wrap text-foreground/90">
                {event.detail}
                {isActivelyStreaming && (
                  <span
                    className="ml-1 inline-block h-4 w-0.5 translate-y-0.5 animate-pulse bg-current motion-reduce:animate-none"
                    aria-hidden
                  />
                )}
              </p>
            </div>
          ) : event.kind === "tool_result" ? (
            <pre className="mt-2 themed-scrollbar max-h-80 max-w-full overflow-auto rounded-md border bg-muted/35 p-3 font-mono text-xs leading-5 whitespace-pre-wrap text-muted-foreground">
              {event.detail}
            </pre>
          ) : (
            <p className="mt-1 max-w-[75ch] text-sm leading-6 break-words whitespace-pre-wrap text-muted-foreground">
              {event.detail}
            </p>
          ))}

        {detailData.length > 0 && (
          <dl className="mt-3 grid gap-2 border-t pt-3 text-xs">
            {detailData.map(([key, value]) => (
              <div key={key} className="grid gap-1 sm:grid-cols-[9rem_1fr]">
                <dt className="font-medium text-muted-foreground">
                  {key.replaceAll("_", " ")}
                </dt>
                <dd className="min-w-0 font-mono leading-5 break-words whitespace-pre-wrap">
                  {formatEventValue(value)}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </article>
    </li>
  )
}

function PhaseOverview({
  session,
  agents,
}: {
  session: ReviewSession
  agents: AgentGroup[]
}) {
  const qualityStarted = session.events.some((event) =>
    /quality|confidence|critique|final review/i.test(event.title)
  )
  const agentsComplete =
    agents.length > 0 && agents.every((agent) => agent.complete)
  const phases = [
    { label: "Context", state: "complete" },
    {
      label: "Parallel analysis",
      state: agentsComplete ? "complete" : agents.length ? "active" : "pending",
    },
    {
      label: "Quality check",
      state:
        session.status === "completed" || session.status === "failed"
          ? "complete"
          : qualityStarted
            ? "active"
            : "pending",
    },
    {
      label: "Published",
      state:
        session.status === "completed"
          ? "complete"
          : session.status === "failed"
            ? "error"
            : "pending",
    },
  ]
  return (
    <ol
      aria-label="Review progress"
      className="grid grid-cols-4 gap-0 border-y py-5"
    >
      {phases.map((phase, index) => (
        <li key={phase.label} className="relative min-w-0 text-center">
          {index > 0 && (
            <span
              className="absolute top-3 right-1/2 h-px w-full bg-border"
              aria-hidden
            />
          )}
          <span
            className={cn(
              "relative z-10 mx-auto flex size-6 items-center justify-center rounded-full border bg-background",
              phase.state === "complete" &&
                "border-emerald-600 bg-emerald-600 text-white",
              phase.state === "active" && "border-sky-500 text-sky-500",
              phase.state === "error" && "border-destructive text-destructive"
            )}
          >
            {phase.state === "complete" ? (
              <Check className="size-3.5" aria-hidden />
            ) : phase.state === "active" ? (
              <CircleDot className="size-3.5" aria-hidden />
            ) : phase.state === "error" ? (
              <AlertCircle className="size-3.5" aria-hidden />
            ) : (
              <Circle className="size-2.5 text-muted-foreground" aria-hidden />
            )}
          </span>
          <span className="mt-2 block min-h-8 px-1 text-xs leading-4 font-medium sm:text-sm">
            {phase.label}
          </span>
        </li>
      ))}
    </ol>
  )
}

function PiTelemetry({ metrics }: { metrics: TraceMetrics }) {
  if (!metrics.pi_events) return null
  const modelLabel = metrics.models.length
    ? metrics.models.join(", ")
    : "Model details unavailable"
  return (
    <section
      className="mb-7 rounded-xl border bg-card p-5"
      aria-labelledby="pi-telemetry-title"
    >
      <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between">
        <div>
          <h2 id="pi-telemetry-title" className="text-sm font-semibold">
            Pi telemetry
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            The worker stream behind this review, including model output,
            reasoning, and repository tools.
          </p>
        </div>
        <span className="font-mono text-xs text-muted-foreground">
          {metrics.pi_events} trace events
        </span>
      </div>
      <dl className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div>
          <dt className="text-xs text-muted-foreground">Pi calls</dt>
          <dd className="mt-1 text-lg font-semibold tabular-nums">
            {metrics.llm_calls}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Tool calls</dt>
          <dd className="mt-1 text-lg font-semibold tabular-nums">
            {metrics.tool_calls}
            {metrics.tool_errors > 0 && (
              <span className="ml-1 text-xs font-normal text-destructive">
                ({metrics.tool_errors} failed)
              </span>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Reasoning</dt>
          <dd className="mt-1 text-lg font-semibold tabular-nums">
            {formatCharacters(metrics.reasoning_chars)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Tokens</dt>
          <dd className="mt-1 text-lg font-semibold tabular-nums">
            {metrics.total_tokens
              ? formatCompactNumber(metrics.total_tokens)
              : "—"}
          </dd>
        </div>
      </dl>
      <div className="mt-4 flex flex-wrap gap-x-5 gap-y-1 border-t pt-3 text-xs text-muted-foreground">
        <span>Output: {formatCharacters(metrics.output_chars)}</span>
        {metrics.input_tokens > 0 && (
          <span>Input: {metrics.input_tokens.toLocaleString()}</span>
        )}
        {metrics.output_tokens > 0 && (
          <span>Generated: {metrics.output_tokens.toLocaleString()}</span>
        )}
        {metrics.cache_read_tokens > 0 && (
          <span>Cache read: {metrics.cache_read_tokens.toLocaleString()}</span>
        )}
        {metrics.duration_ms > 0 && (
          <span>Agent time: {formatMilliseconds(metrics.duration_ms)}</span>
        )}
        <span className="min-w-0 truncate" title={modelLabel}>
          Model: {modelLabel}
        </span>
      </div>
    </section>
  )
}

function AgentWorkspace({
  agents,
  selectedKey,
  onSelect,
  running,
}: {
  agents: AgentGroup[]
  selectedKey: string
  onSelect: (key: string) => void
  running: boolean
}) {
  const selected =
    agents.find((agent) => agent.key === selectedKey) || agents[0]
  if (!selected)
    return (
      <div className="border border-dashed px-6 py-12 text-center text-sm text-muted-foreground">
        Agent workspaces will appear when analysis begins.
      </div>
    )
  const events = mergeReasoning(selected.events)
  const end =
    selected.finishedAt ||
    selected.events.at(-1)?.created_at ||
    selected.startedAt
  const actions = selected.events.filter(
    (event) => event.kind === "action"
  ).length
  return (
    <div>
      <div
        className="flex gap-2 overflow-x-auto pb-2"
        role="tablist"
        aria-label="Review agents"
      >
        {agents.map((agent) => {
          const active = agent.key === selected.key
          return (
            <button
              key={agent.key}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => onSelect(agent.key)}
              className={cn(
                "min-w-44 flex-1 rounded-lg border px-4 py-3 text-left transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                active ? "border-sky-500/60 bg-sky-500/5" : "hover:bg-muted/40"
              )}
            >
              <span className="flex items-center justify-between gap-3">
                <span className="text-sm font-medium">
                  {agentName(agent.pass)}
                </span>
                {agent.complete ? (
                  <Check
                    className="size-4 text-emerald-600"
                    aria-label="Complete"
                  />
                ) : (
                  <LoaderCircle
                    className="size-4 animate-spin text-sky-500 motion-reduce:animate-none"
                    aria-label="Running"
                  />
                )}
              </span>
              <span className="mt-1 block text-xs text-muted-foreground">
                {agent.complete ? "Complete" : "Working"} ·{" "}
                {formatDuration(agent.startedAt, end)}
              </span>
            </button>
          )
        })}
      </div>

      <div className="mt-4 border-t pt-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold">
                {agentName(selected.pass)}
              </h3>
              <Badge variant="secondary" className="gap-1.5">
                {selected.complete ? (
                  <Check className="size-3" aria-hidden />
                ) : (
                  <LoaderCircle
                    className="size-3 animate-spin motion-reduce:animate-none"
                    aria-hidden
                  />
                )}
                {selected.complete
                  ? "Complete"
                  : running
                    ? "Working"
                    : "Stopped"}
              </Badge>
            </div>
            <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
              {agentDescriptions[selected.pass] ||
                "Runs a focused part of the review."}
            </p>
          </div>
          <dl className="flex gap-6 text-sm">
            <div>
              <dt className="text-xs text-muted-foreground">Actions</dt>
              <dd className="mt-1 font-medium tabular-nums">{actions}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Findings</dt>
              <dd className="mt-1 font-medium tabular-nums">
                {selected.findings}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Duration</dt>
              <dd className="mt-1 font-mono text-xs">
                {formatDuration(selected.startedAt, end)}
              </dd>
            </div>
          </dl>
        </div>
        <div className="mt-6 rounded-lg bg-muted/35 px-4 py-3 text-sm">
          <span className="font-medium">Outcome: </span>
          <span className="text-muted-foreground">
            {selected.complete
              ? selected.findings
                ? `${selected.findings} candidate finding${selected.findings === 1 ? "" : "s"} returned to the review pipeline.`
                : "Completed without candidate findings."
              : "Analysis is still in progress."}
          </span>
        </div>
        <h4 className="mt-7 mb-4 text-sm font-semibold">Activity</h4>
        <ol aria-live="polite" aria-relevant="additions">
          {events.map((event, index) => (
            <EventRow
              key={event.id}
              event={event}
              isLast={index === events.length - 1}
              streaming={
                running && !selected.complete && index === events.length - 1
              }
            />
          ))}
        </ol>
      </div>
    </div>
  )
}

export function ReviewSessionPage() {
  const { sessionId = "" } = useParams()
  const navigate = useNavigate()
  const [session, setSession] = useState<ReviewSession | null>(null)
  const [error, setError] = useState("")
  const [connected, setConnected] = useState(false)
  const [following, setFollowing] = useState(true)
  const [view, setView] = useState<TraceView>("agents")
  const [selectedAgent, setSelectedAgent] = useState("")
  const [confirmRetrigger, setConfirmRetrigger] = useState(false)
  const [retriggering, setRetriggering] = useState(false)
  const [retriggerError, setRetriggerError] = useState("")
  const bottomRef = useRef<HTMLDivElement>(null)
  useDocumentTitle(session ? `PR #${session.pr_number} review` : "Review")

  useEffect(() => {
    let source: EventSource | null = null
    let cancelled = false
    fetchJson<ReviewSession>(`/api/reviews/${sessionId}`)
      .then((data) => {
        if (cancelled) return
        setSession((current) => {
          if (!current) return data
          const events = new Map(
            [...data.events, ...current.events].map((event) => [
              event.id,
              event,
            ])
          )
          return {
            ...data,
            status: current.status,
            finished_at: current.finished_at || data.finished_at,
            events: [...events.values()].sort((a, b) => a.id - b.id),
          }
        })
      })
      .catch(() => setError("This review session could not be found."))
    source = new EventSource(`${API_BASE}/api/events`, {
      withCredentials: true,
    })
    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false)
    source.addEventListener("review_trace", (raw) => {
      const message = JSON.parse((raw as MessageEvent).data)
      if (message.session_id === sessionId)
        setSession(
          (current) =>
            current &&
            (current.events.some((event) => event.id === message.event.id)
              ? current
              : {
                  ...current,
                  events: [...current.events, message.event],
                })
        )
    })
    source.addEventListener("review_trace_status", (raw) => {
      const message = JSON.parse((raw as MessageEvent).data)
      if (message.session_id === sessionId)
        setSession(
          (current) =>
            current && {
              ...current,
              status: message.status,
              finished_at:
                message.status === "completed" ||
                message.status === "failed" ||
                message.status === "interrupted"
                  ? current.finished_at || Date.now() / 1000
                  : null,
            }
        )
    })
    return () => {
      cancelled = true
      source?.close()
    }
  }, [sessionId])

  const agents = useMemo(() => {
    const groups = new Map<string, AgentGroup>()
    for (const event of session?.events || []) {
      if (event.data.source === "pi") continue
      const pass = typeof event.data.pass === "string" ? event.data.pass : null
      const agentId =
        typeof event.data.agent_id === "number" ? event.data.agent_id : null
      if (!pass || agentId === null) continue
      const key = `${pass}:${agentId}`
      const existing = groups.get(key) || {
        key,
        pass,
        agentId,
        events: [],
        complete: false,
        startedAt: event.created_at,
        finishedAt: null,
        findings: 0,
      }
      existing.events.push(event)
      existing.startedAt = Math.min(existing.startedAt, event.created_at)
      if (
        (event.kind === "decision" && /complete$/i.test(event.title)) ||
        event.kind === "error"
      ) {
        existing.complete = true
        existing.finishedAt = event.created_at
      }
      if (Array.isArray(event.data.findings)) {
        existing.findings = Math.max(
          existing.findings,
          event.data.findings.length
        )
      }
      groups.set(key, existing)
    }
    const order: Record<string, number> = {
      walkthrough: 0,
      review: 1,
      "security review": 2,
      "quality critique": 3,
    }
    return [...groups.values()].sort(
      (a, b) =>
        (order[a.pass] ?? 10) - (order[b.pass] ?? 10) || a.agentId - b.agentId
    )
  }, [session])

  const effectiveSelectedAgent =
    selectedAgent ||
    agents.find((agent) => agent.pass === "review")?.key ||
    agents[0]?.key ||
    ""

  const findings = useMemo(
    () =>
      session?.events.reduce(
        (count, event) =>
          Array.isArray(event.data.findings)
            ? event.data.findings.length
            : count,
        0
      ) || 0,
    [session]
  )
  const allEvents = useMemo(
    () => mergeReasoning(session?.events || []),
    [session]
  )
  const piEvents = useMemo(
    () =>
      mergeReasoning(
        (session?.events || []).filter((event) => event.data.source === "pi")
      ),
    [session]
  )
  const traceMetrics = useMemo(
    () => traceMetricsFromEvents(session?.events || []),
    [session]
  )
  const milestones = useMemo(
    () =>
      (session?.events || []).filter((event) => {
        const isAgent = typeof event.data.pass === "string"
        if (!isAgent) return event.kind !== "reasoning"
        return (
          (event.kind === "action" &&
            (/^Starting Pi /i.test(event.title) ||
              /started$/i.test(event.title))) ||
          (event.kind === "decision" && /complete$/i.test(event.title)) ||
          event.kind === "error"
        )
      }),
    [session]
  )

  useEffect(() => {
    const updateFollowing = () => {
      const distanceFromBottom =
        document.documentElement.scrollHeight -
        window.innerHeight -
        window.scrollY
      setFollowing(distanceFromBottom < 160)
    }
    window.addEventListener("scroll", updateFollowing, { passive: true })
    return () => window.removeEventListener("scroll", updateFollowing)
  }, [])

  useEffect(() => {
    if (
      !following ||
      !["agents", "pi", "all"].includes(view) ||
      session?.status !== "running"
    )
      return
    const frame = requestAnimationFrame(() =>
      bottomRef.current?.scrollIntoView({ block: "end" })
    )
    return () => cancelAnimationFrame(frame)
  }, [session?.events.length, session?.status, following, view])

  const retrigger = async () => {
    setRetriggering(true)
    setRetriggerError("")
    try {
      const response = await postJson<{ replacement_session_id: string }>(
        `/api/reviews/${sessionId}/retrigger`,
        {}
      )
      setConfirmRetrigger(false)
      navigate(`/reviews/${response.replacement_session_id}`)
    } catch (requestError) {
      setRetriggerError(
        requestError instanceof Error
          ? requestError.message
          : "The review could not be started."
      )
      setConfirmRetrigger(false)
    } finally {
      setRetriggering(false)
    }
  }

  if (error)
    return (
      <div className="mx-auto max-w-3xl py-20 text-center">
        <AlertCircle className="mx-auto mb-4 size-8 text-muted-foreground" />
        <h1 className="text-lg font-semibold">Review unavailable</h1>
        <p className="mt-2 text-sm text-muted-foreground">{error}</p>
      </div>
    )
  if (!session)
    return (
      <div className="mx-auto max-w-5xl space-y-5 py-8">
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-80 w-full" />
      </div>
    )

  const running = session.status === "running"
  const active = running || session.status === "queued"
  const interrupted = session.status === "interrupted"
  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 lg:py-9">
      <header className="pb-6">
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <span>
            {session.owner}/{session.repo}
          </span>
          <span aria-hidden>·</span>
          <span>PR #{session.pr_number}</span>
        </div>
        <div className="mt-2 flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="max-w-3xl text-2xl font-semibold tracking-tight text-balance">
              {session.pr_title}
            </h1>
            <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
              <Badge
                className={cn(
                  "gap-1.5",
                  running
                    ? "bg-sky-500/10 text-sky-700 dark:text-sky-400"
                    : session.status === "queued"
                      ? "bg-violet-500/10 text-violet-700 dark:text-violet-300"
                      : session.status === "completed"
                        ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                        : interrupted
                          ? "bg-amber-500/10 text-amber-800 dark:text-amber-300"
                          : "bg-destructive/10 text-destructive"
                )}
                variant="secondary"
              >
                {running || session.status === "queued" ? (
                  <LoaderCircle className="size-3.5 animate-spin motion-reduce:animate-none" />
                ) : session.status === "completed" ? (
                  <Check className="size-3.5" />
                ) : interrupted ? (
                  <PauseCircle className="size-3.5" />
                ) : (
                  <AlertCircle className="size-3.5" />
                )}
                {running
                  ? "Reviewing"
                  : session.status === "queued"
                    ? "Queued"
                    : session.status === "completed"
                      ? "Complete"
                      : interrupted
                        ? "Interrupted"
                        : "Failed"}
              </Badge>
              {active && (
                <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <CircleDot
                    className={cn("size-3", connected && "text-emerald-600")}
                  />
                  {connected ? "Live updates connected" : "Reconnecting…"}
                </span>
              )}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              onClick={() => setConfirmRetrigger(true)}
              disabled={active}
            >
              <RefreshCw className="size-4" />
              {active ? "Review active" : "Run review again"}
            </Button>
            <Button asChild variant="outline">
              <a href={session.pr_url} target="_blank" rel="noreferrer">
                Open pull request <ExternalLink className="size-4" />
              </a>
            </Button>
          </div>
        </div>
        {retriggerError && (
          <p className="mt-4 text-sm text-destructive" role="alert">
            {retriggerError}
          </p>
        )}
      </header>

      {interrupted && (
        <section
          className="mb-6 flex flex-col gap-3 rounded-lg border border-amber-500/25 bg-amber-500/10 p-4 text-sm sm:flex-row sm:items-center sm:justify-between"
          aria-labelledby="interruption-title"
        >
          <div>
            <h2
              id="interruption-title"
              className="font-medium text-amber-900 dark:text-amber-200"
            >
              This review did not finish
            </h2>
            <p className="mt-1 max-w-3xl text-amber-900/80 dark:text-amber-200/80">
              {session.recovery_reason ||
                session.error ||
                "The review process was interrupted before it could publish a final result."}
            </p>
            {session.finished_at && (
              <p className="mt-1 text-xs text-amber-900/70 dark:text-amber-200/70">
                Interrupted{" "}
                {new Date(session.finished_at * 1000).toLocaleString()}
              </p>
            )}
          </div>
          <Button variant="outline" onClick={() => setConfirmRetrigger(true)}>
            <RefreshCw className="size-4" />
            Retry review
          </Button>
        </section>
      )}

      {(session.retry_of || session.replacement_id) && (
        <nav
          className="mb-6 flex flex-wrap gap-x-4 gap-y-2 text-sm"
          aria-label="Related review runs"
        >
          {session.retry_of && (
            <Button
              variant="link"
              className="h-auto p-0"
              onClick={() => navigate(`/reviews/${session.retry_of}`)}
            >
              View previous attempt
            </Button>
          )}
          {session.replacement_id && (
            <Button
              variant="link"
              className="h-auto p-0"
              onClick={() => navigate(`/reviews/${session.replacement_id}`)}
            >
              View replacement attempt
            </Button>
          )}
        </nav>
      )}

      <PhaseOverview session={session} agents={agents} />
      <PiTelemetry metrics={traceMetrics} />

      <div className="grid gap-8 py-7 lg:grid-cols-[minmax(0,1fr)_15rem]">
        <section className="min-w-0" aria-labelledby="trace-title">
          <div className="mb-6 flex flex-col gap-4 border-b pb-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 id="trace-title" className="text-base font-semibold">
                Review trace
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Follow each agent separately or inspect shared review
                milestones.
              </p>
            </div>
            <Tabs
              value={view}
              onValueChange={(value) => setView(value as TraceView)}
            >
              <TabsList aria-label="Trace view">
                <TabsTrigger value="agents">Agents</TabsTrigger>
                <TabsTrigger value="milestones">Milestones</TabsTrigger>
                {piEvents.length > 0 && (
                  <TabsTrigger value="pi">Pi internals</TabsTrigger>
                )}
                <TabsTrigger value="all">All activity</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>

          {view === "agents" && (
            <AgentWorkspace
              agents={agents}
              selectedKey={effectiveSelectedAgent}
              onSelect={setSelectedAgent}
              running={running}
            />
          )}
          {view === "milestones" && (
            <ol aria-live="polite" aria-relevant="additions">
              {milestones.map((event, index) => (
                <EventRow
                  key={event.id}
                  event={event}
                  isLast={index === milestones.length - 1}
                  showAgent
                />
              ))}
            </ol>
          )}
          {view === "pi" && (
            <>
              <div className="mb-5 flex items-start gap-3 rounded-lg bg-violet-500/5 p-4 text-sm text-muted-foreground">
                <Brain
                  className="mt-0.5 size-4 shrink-0 text-violet-600"
                  aria-hidden
                />
                <p>
                  Follow the model response as it is written, with repository
                  tools and worker events kept in sequence around it.
                </p>
              </div>
              <ol aria-label="Pi model activity">
                {piEvents.map((event, index) => (
                  <EventRow
                    key={event.id}
                    event={event}
                    isLast={index === piEvents.length - 1}
                    showAgent
                    streaming={running && index === piEvents.length - 1}
                  />
                ))}
              </ol>
            </>
          )}
          {view === "all" && (
            <>
              <div className="mb-5 flex items-start gap-3 rounded-lg bg-muted/40 p-4 text-sm text-muted-foreground">
                <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden />
                <p>
                  This diagnostic view preserves the exact event order across
                  every parallel agent.
                </p>
              </div>
              <ol aria-label="All review activity">
                {allEvents.map((event, index) => (
                  <EventRow
                    key={event.id}
                    event={event}
                    isLast={index === allEvents.length - 1}
                    showAgent
                    streaming={running && index === allEvents.length - 1}
                  />
                ))}
              </ol>
            </>
          )}
          <div ref={bottomRef} aria-hidden />
        </section>

        <aside className="h-fit border-t pt-5 lg:sticky lg:top-20 lg:border-t-0 lg:border-l lg:pl-6">
          <h2 className="text-sm font-medium">Session</h2>
          <dl className="mt-4 space-y-4 text-sm">
            <div>
              <dt className="text-xs text-muted-foreground">Agents</dt>
              <dd className="mt-1 font-medium tabular-nums">
                {agents.filter((agent) => agent.complete).length} of{" "}
                {agents.length} complete
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Findings</dt>
              <dd className="mt-1 font-medium tabular-nums">{findings}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Commit</dt>
              <dd className="mt-1 font-mono text-xs">
                {session.head_sha.slice(0, 8)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Started</dt>
              <dd className="mt-1">
                {new Date(session.started_at * 1000).toLocaleString()}
              </dd>
            </div>
          </dl>
        </aside>
      </div>

      {running && !following && ["agents", "pi", "all"].includes(view) && (
        <Button
          type="button"
          className="fixed right-5 bottom-5 z-30 gap-2 rounded-full shadow-lg sm:right-7 sm:bottom-7"
          onClick={() => {
            setFollowing(true)
            bottomRef.current?.scrollIntoView({ block: "end" })
          }}
        >
          <ArrowDown className="size-4" aria-hidden />
          Jump to latest
        </Button>
      )}

      <ConfirmDialog
        open={confirmRetrigger}
        onOpenChange={setConfirmRetrigger}
        title="Run this review again?"
        description="Mira will review the latest commit on this pull request and may update its walkthrough or post new findings."
        confirmLabel="Run review"
        loading={retriggering}
        onConfirm={retrigger}
      />
    </main>
  )
}
