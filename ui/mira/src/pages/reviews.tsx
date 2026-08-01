import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  CircleDot,
  Clock3,
  ExternalLink,
  PauseCircle,
  Search,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { Link } from "react-router"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type ReviewTraceSummary } from "@/lib/api"
import { API_BASE } from "@/lib/api/http"
import { useAsync, useDocumentTitle } from "@/lib/hooks"
import { cn } from "@/lib/utils"

type StatusPresentation = {
  label: string
  icon: typeof CircleDot
  className: string
}

const statusPresentation: Record<string, StatusPresentation> = {
  queued: {
    label: "Queued",
    icon: Clock3,
    className:
      "border-violet-500/25 bg-violet-500/10 text-violet-800 dark:text-violet-300",
  },
  running: {
    label: "Running",
    icon: CircleDot,
    className: "border-sky-500/25 bg-sky-500/10 text-sky-800 dark:text-sky-300",
  },
  completed: {
    label: "Complete",
    icon: CheckCircle2,
    className:
      "border-emerald-500/25 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300",
  },
  failed: {
    label: "Failed",
    icon: AlertCircle,
    className: "border-destructive/25 bg-destructive/10 text-destructive",
  },
  interrupted: {
    label: "Interrupted",
    icon: PauseCircle,
    className:
      "border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-300",
  },
}

const fallbackStatusPresentation: StatusPresentation = {
  label: "Unknown",
  icon: CircleDot,
  className: "border-muted-foreground/25 bg-muted text-muted-foreground",
}

function titleCase(value: string) {
  return value.replace(/(^|\s)\S/g, (letter) => letter.toUpperCase())
}

function statusLabel(status: unknown) {
  if (typeof status !== "string" || !status.trim()) {
    return fallbackStatusPresentation.label
  }
  return titleCase(status.trim().replace(/[-_]+/g, " "))
}

function relativeTime(timestamp: number, now: number) {
  const seconds = Math.max(0, Math.floor(now / 1000 - timestamp))
  if (seconds < 10) return "just now"
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

function duration(session: ReviewTraceSummary, now: number) {
  const end = session.finished_at || now / 1000
  const seconds = Math.max(0, Math.floor(end - session.started_at))
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

function StatusBadge({ status }: { status: ReviewTraceSummary["status"] }) {
  const normalizedStatus = typeof status === "string" ? status : ""
  const presentation = statusPresentation[normalizedStatus] ?? {
    ...fallbackStatusPresentation,
    label: statusLabel(status),
  }
  const Icon = presentation.icon
  return (
    <Badge className={cn("gap-1.5", presentation.className)}>
      <Icon className="size-3" aria-hidden />
      {presentation.label}
    </Badge>
  )
}

function CurrentAgent({ session }: { session: ReviewTraceSummary }) {
  if (!session.current_pass)
    return <span className="text-muted-foreground">Preparing review</span>
  return (
    <span>
      {titleCase(session.current_pass)}
      {session.current_agent ? ` · Agent ${session.current_agent}` : ""}
    </span>
  )
}

export function ReviewsPage() {
  const [search, setSearch] = useState("")
  const [status, setStatus] = useState("all")
  const [now, setNow] = useState(0)
  const [refreshKey, setRefreshKey] = useState(0)
  useDocumentTitle("Reviews")

  const {
    data: sessions,
    loading,
    error,
  } = useAsync(() => api.listReviewTraces(), [refreshKey])

  useEffect(() => {
    const source = new EventSource(`${API_BASE}/api/events`, {
      withCredentials: true,
    })
    let refreshTimer: number | undefined
    const refresh = () => {
      window.clearTimeout(refreshTimer)
      refreshTimer = window.setTimeout(
        () => setRefreshKey((key) => key + 1),
        500
      )
    }
    source.addEventListener("review_trace", refresh)
    source.addEventListener("review_trace_status", refresh)
    return () => {
      window.clearTimeout(refreshTimer)
      source.close()
    }
  }, [])

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase()
    return (sessions || []).filter((session) => {
      if (status !== "all" && session.status !== status) return false
      if (!query) return true
      return `${session.owner}/${session.repo} #${session.pr_number} ${session.pr_title}`
        .toLowerCase()
        .includes(query)
    })
  }, [search, sessions, status])
  const active = filtered.filter(
    (session) => session.status === "queued" || session.status === "running"
  )
  const history = filtered.filter(
    (session) => session.status !== "queued" && session.status !== "running"
  )

  const initialLoading = loading && !sessions

  return (
    <main className="mx-auto w-full max-w-7xl space-y-8 px-4 py-6 sm:px-6 lg:py-9">
      <header className="flex flex-col gap-5 border-b pb-7 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Review runs</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Watch active reviews and inspect the reasoning, agents, and
            decisions behind previous runs.
          </p>
        </div>
        <div className="flex gap-5 text-sm">
          <div>
            <div className="font-semibold tabular-nums">{active.length}</div>
            <div className="text-xs text-muted-foreground">Active</div>
          </div>
          <div>
            <div className="font-semibold tabular-nums">
              {sessions?.length || 0}
            </div>
            <div className="text-xs text-muted-foreground">Recorded</div>
          </div>
        </div>
      </header>

      {error && (
        <div className="flex items-center justify-between gap-4 rounded-lg border border-destructive/20 bg-destructive/5 p-4 text-sm text-destructive">
          <span>Review runs could not be loaded.</span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setRefreshKey((key) => key + 1)}
          >
            Retry
          </Button>
        </div>
      )}

      {initialLoading ? (
        <div className="space-y-5" aria-label="Loading review runs">
          <Skeleton className="h-36 w-full rounded-xl" />
          <Skeleton className="h-10 w-full max-w-md" />
          <Skeleton className="h-72 w-full rounded-xl" />
        </div>
      ) : (
        <>
          {active.length > 0 && (
            <section aria-labelledby="active-reviews-title">
              <div className="mb-4 flex items-center gap-2">
                <span className="size-2 rounded-full bg-sky-500" aria-hidden />
                <h2 id="active-reviews-title" className="text-sm font-semibold">
                  Active reviews
                </h2>
              </div>
              <div className="grid gap-3 lg:grid-cols-2">
                {active.map((session) => (
                  <Link
                    key={session.id}
                    to={`/reviews/${session.id}`}
                    className="group rounded-xl border bg-card p-5 transition-colors hover:border-sky-500/40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                  >
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0">
                        <div className="text-xs font-medium text-muted-foreground">
                          {session.owner}/{session.repo} · PR #
                          {session.pr_number}
                        </div>
                        <h3 className="mt-1 truncate font-medium">
                          {session.pr_title}
                        </h3>
                      </div>
                      <StatusBadge status={session.status} />
                    </div>
                    <div className="mt-5 flex items-end justify-between gap-4">
                      <div>
                        <div className="text-xs text-muted-foreground">
                          Current agent
                        </div>
                        <div className="mt-1 text-sm font-medium">
                          <CurrentAgent session={session} />
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="font-mono text-sm tabular-nums">
                          {duration(session, now)}
                        </div>
                        <div className="mt-1 text-xs text-muted-foreground">
                          {session.findings} findings so far
                        </div>
                      </div>
                    </div>
                    <div className="mt-5 flex items-center justify-between border-t pt-4 text-xs text-muted-foreground">
                      <span>
                        {session.completed_agents} agents complete ·{" "}
                        {session.event_count} events
                      </span>
                      <span className="flex items-center gap-1 font-medium text-foreground">
                        Watch live{" "}
                        <ArrowRight
                          className="size-3.5 transition-transform group-hover:translate-x-0.5"
                          aria-hidden
                        />
                      </span>
                    </div>
                  </Link>
                ))}
              </div>
            </section>
          )}

          <section aria-labelledby="review-history-title">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <h2
                  id="review-history-title"
                  className="text-base font-semibold"
                >
                  Recent runs
                </h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  One row per review execution.
                </p>
              </div>
              <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row">
                <div className="relative sm:w-72">
                  <Search
                    className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
                    aria-hidden
                  />
                  <Input
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder="Search PRs or repositories"
                    className="pl-9"
                    aria-label="Search review runs"
                  />
                </div>
                <Select value={status} onValueChange={setStatus}>
                  <SelectTrigger
                    className="w-full sm:w-36"
                    aria-label="Filter by status"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All statuses</SelectItem>
                    <SelectItem value="completed">Complete</SelectItem>
                    <SelectItem value="failed">Failed</SelectItem>
                    <SelectItem value="interrupted">Interrupted</SelectItem>
                    <SelectItem value="queued">Queued</SelectItem>
                    <SelectItem value="running">Running</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            {history.length === 0 ? (
              <div className="mt-5 rounded-xl border border-dashed px-6 py-16 text-center">
                <CircleDot
                  className="mx-auto size-7 text-muted-foreground"
                  aria-hidden
                />
                <h3 className="mt-4 text-sm font-medium">
                  No review runs found
                </h3>
                <p className="mt-1 text-sm text-muted-foreground">
                  {active.length
                    ? "Active runs are shown above."
                    : sessions?.length
                      ? "Try changing the search or status filter."
                      : "Review traces will appear here when Mira reviews a pull request."}
                </p>
              </div>
            ) : (
              <div className="mt-5 overflow-hidden rounded-xl border">
                <div className="hidden grid-cols-[minmax(18rem,1fr)_8rem_9rem_7rem_5rem_2rem] gap-4 border-b bg-muted/35 px-4 py-2.5 text-xs font-medium text-muted-foreground md:grid">
                  <span>Pull request</span>
                  <span>Status</span>
                  <span>Last agent</span>
                  <span>Duration</span>
                  <span>Findings</span>
                  <span />
                </div>
                <div className="divide-y">
                  {history.map((session) => (
                    <div
                      key={session.id}
                      className="grid gap-3 px-4 py-4 md:grid-cols-[minmax(18rem,1fr)_8rem_9rem_7rem_5rem_2rem] md:items-center md:gap-4"
                    >
                      <div className="min-w-0">
                        <Link
                          to={`/reviews/${session.id}`}
                          className="font-medium hover:underline focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-ring"
                        >
                          {session.pr_title}
                        </Link>
                        <div className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
                          <span>
                            {session.owner}/{session.repo} · #
                            {session.pr_number}
                          </span>
                          <span className="font-mono">
                            {session.head_sha.slice(0, 8)}
                          </span>
                          <span>{relativeTime(session.started_at, now)}</span>
                        </div>
                        {(session.status === "failed" ||
                          session.status === "interrupted") &&
                          session.error && (
                            <p
                              className={cn(
                                "mt-2 line-clamp-1 text-xs",
                                session.status === "failed"
                                  ? "text-destructive"
                                  : "text-amber-800 dark:text-amber-300"
                              )}
                            >
                              {session.error}
                            </p>
                          )}
                      </div>
                      <div>
                        <StatusBadge status={session.status} />
                      </div>
                      <div className="text-sm">
                        <div className="mb-1 text-xs text-muted-foreground md:hidden">
                          Last agent
                        </div>
                        <CurrentAgent session={session} />
                      </div>
                      <div className="font-mono text-sm tabular-nums">
                        <div className="mb-1 font-sans text-xs text-muted-foreground md:hidden">
                          Duration
                        </div>
                        {duration(session, now)}
                      </div>
                      <div className="text-sm tabular-nums">
                        <div className="mb-1 text-xs text-muted-foreground md:hidden">
                          Findings
                        </div>
                        {session.findings}
                      </div>
                      <div className="flex items-center gap-2 md:justify-end">
                        <Button variant="ghost" size="icon-sm" asChild>
                          <Link
                            to={`/reviews/${session.id}`}
                            aria-label={`Open trace for ${session.pr_title}`}
                          >
                            <ArrowRight className="size-4" />
                          </Link>
                        </Button>
                        <Button variant="ghost" size="icon-sm" asChild>
                          <a
                            href={session.pr_url}
                            target="_blank"
                            rel="noreferrer"
                            aria-label={`Open pull request ${session.pr_number}`}
                          >
                            <ExternalLink className="size-4" />
                          </a>
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>
        </>
      )}
    </main>
  )
}
