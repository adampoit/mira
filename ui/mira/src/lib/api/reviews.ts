import { fetchJson } from "./http"
import type { ReviewTraceSummary } from "./types"

export const reviewsApi = {
  listReviewTraces: (limit = 500) =>
    fetchJson<ReviewTraceSummary[]>(`/api/reviews?limit=${limit}`),
}
