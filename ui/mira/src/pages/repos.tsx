import { AlertCircle, Loader2, Search } from "lucide-react"
import { useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router"

import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { api, type RepoListItem } from "@/lib/api"
import { useDocumentTitle } from "@/lib/hooks"

export function ReposPage() {
  useDocumentTitle("Repositories")
  const [repos, setRepos] = useState<RepoListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [searchParams] = useSearchParams()
  // Seed the filter from `?owner=` so breadcrumb links can pre-filter the list.
  const [search, setSearch] = useState(searchParams.get("owner") ?? "")

  // Initial load + poll while any repo is indexing
  useEffect(() => {
    const load = () => {
      api
        .listRepos()
        .then(setRepos)
        .finally(() => setLoading(false))
    }
    load()
    const interval = setInterval(load, 5000)
    return () => clearInterval(interval)
  }, [])

  const hasIndexing = repos.some((r) => r.status === "indexing")
  const failedRepos = repos.filter((r) => r.status === "failed")

  const filtered = repos.filter(
    (r) =>
      r.owner.toLowerCase().includes(search.toLowerCase()) ||
      r.repo.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Repositories</h1>
        <p className="text-sm text-muted-foreground">
          All repositories and their indexing status
        </p>
      </div>

      {failedRepos.length > 0 && (
        <section
          role="alert"
          aria-labelledby="indexing-failures-title"
          className="rounded-lg border border-destructive/30 bg-destructive/5 p-4"
        >
          <div className="flex items-start gap-3">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-destructive/10 text-destructive">
              <AlertCircle className="size-4" aria-hidden />
            </span>
            <div className="min-w-0 flex-1">
              <h2
                id="indexing-failures-title"
                className="text-sm font-semibold"
              >
                {failedRepos.length === 1
                  ? "A repository index needs attention"
                  : `${failedRepos.length} repository indexes need attention`}
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Reviews can miss cross-file context until indexing succeeds.
                Open a repository to inspect the error and retry.
              </p>
              <ul className="mt-4 divide-y border-y">
                {failedRepos.map((failedRepo) => (
                  <li
                    key={`${failedRepo.platform}:${failedRepo.owner}/${failedRepo.repo}`}
                  >
                    <Link
                      to={`/repos/${failedRepo.owner}/${failedRepo.repo}`}
                      className="block py-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                    >
                      <span className="text-sm font-medium">
                        {failedRepo.owner}/{failedRepo.repo}
                      </span>
                      <span className="mt-1 block max-w-4xl text-sm leading-5 break-words whitespace-pre-wrap text-destructive">
                        {failedRepo.error ||
                          "Indexing stopped before completion."}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>
      )}

      {hasIndexing && (
        <Card className="relative overflow-hidden border-primary/30 bg-primary/5">
          <div className="absolute inset-x-0 top-0 h-px [animation:shimmer_2s_linear_infinite] bg-gradient-to-r from-transparent via-primary to-transparent [background-size:200%_100%]" />
          <CardContent className="flex items-center gap-4 p-4">
            <div className="relative flex h-8 w-8 items-center justify-center">
              <div className="absolute inset-1 [animation:spin_1s_linear_infinite] rounded-full border-2 border-primary/40 border-t-primary" />
            </div>
            <p className="text-sm font-medium">Indexing in progress...</p>
          </CardContent>
        </Card>
      )}

      <div className="relative max-w-sm">
        <Search className="absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          placeholder="Search repositories..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="pl-9"
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            {loading ? (
              <Skeleton className="h-6 w-32" />
            ) : (
              `${filtered.length} repositories`
            )}
          </CardTitle>
          <CardDescription>Click a repository to view details</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="space-y-4">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="flex items-center">
                  <Skeleton className="h-9 w-9 rounded-full" />
                  <div className="ml-4 space-y-2">
                    <Skeleton className="h-4 w-40" />
                    <Skeleton className="h-3 w-24" />
                  </div>
                  <Skeleton className="ml-auto h-5 w-16" />
                </div>
              ))}
            </div>
          ) : filtered.length > 0 ? (
            <div className="space-y-4">
              {filtered.map((r) => {
                const key = `${r.owner}/${r.repo}`
                const initials = r.repo
                  .split("-")
                  .map((w) => w[0])
                  .join("")
                  .toUpperCase()
                  .slice(0, 2)

                return (
                  <Link
                    key={key}
                    to={`/repos/${r.owner}/${r.repo}`}
                    className="flex items-start rounded-md p-2 transition-colors hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                  >
                    <Avatar className="h-9 w-9">
                      <AvatarFallback>{initials}</AvatarFallback>
                    </Avatar>
                    <div className="ml-4 min-w-0 flex-1 space-y-1">
                      <p className="text-sm leading-none font-medium">
                        {r.repo}
                      </p>
                      <p className="text-sm text-muted-foreground">{r.owner}</p>
                      {r.status === "failed" && (
                        <p className="max-w-3xl pt-1 text-xs leading-5 break-words whitespace-pre-wrap text-destructive">
                          {r.error || "Indexing stopped before completion."}
                        </p>
                      )}
                    </div>
                    <div className="ml-4 flex shrink-0 items-center gap-2">
                      <StatusBadge status={r.status} error={r.error} />
                      {r.status === "ready" && (
                        <span className="text-sm font-medium">
                          {r.file_count} files
                        </span>
                      )}
                    </div>
                  </Link>
                )
              })}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              No repositories found.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function StatusBadge({ status, error }: { status: string; error?: string }) {
  switch (status) {
    case "indexing":
      return (
        <Badge variant="secondary" className="gap-1">
          <Loader2 className="h-3 w-3 animate-spin" />
          Indexing
        </Badge>
      )
    case "ready":
      return null
    case "empty":
      return (
        <Badge
          variant="outline"
          className="text-muted-foreground"
          title={error}
        >
          Empty
        </Badge>
      )
    case "failed":
      return (
        <Badge variant="destructive" title={error}>
          Failed
        </Badge>
      )
    default:
      return (
        <Badge variant="outline" className="text-muted-foreground">
          Pending
        </Badge>
      )
  }
}
