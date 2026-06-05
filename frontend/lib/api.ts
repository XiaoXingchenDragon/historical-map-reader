export const apiBase = process.env.NEXT_PUBLIC_API_BASE || 'http://127.0.0.1:8000'

export type Book = {
  id: number
  title: string
  author: string
  created_at: string
}

export type LocalFile = {
  id: string
  path: string
  name: string
  size: number
  type: string
}

export type ChapterSummary = {
  id: number
  title: string
  order_index: number
}

export type Mention = {
  id: number
  raw_name: string
  start_offset: number
  end_offset: number
  lat: number | null
  lng: number | null
  place_id: number | null
  canonical_name: string | null
  confidence: number
  paragraph_id?: number
}

export type PlaceCandidate = {
  id: string
  paragraph_id: number
  raw_name: string
  start_offset: number
  end_offset: number
  reason: string
}

export type Paragraph = {
  paragraph_id: number
  text: string
  mentions: Mention[]
  candidates: PlaceCandidate[]
}

export type Chapter = {
  id: number
  title: string
  book_id: number
  paragraphs: Paragraph[]
}

export type PlaceCount = {
  place_id: number
  canonical_name: string
  lat: number
  lng: number
  count: number
}

export type ProcessProgress = {
  book_id: number
  stage: string
  percent: number
  current: number
  total: number
  detail: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, init)
  if (!response.ok) {
    const contentType = response.headers.get('content-type') || ''
    const body = contentType.includes('application/json')
      ? await response.json().catch(() => null)
      : await response.text().catch(() => '')
    const detail = typeof body === 'string' ? body : body?.detail
    throw new Error(detail || response.statusText)
  }
  return response.json() as Promise<T>
}

export function fetchBooks() {
  return request<Book[]>('/api/books')
}

export function deleteBook(bookId: number) {
  return request<{ book_id: number; deleted: boolean; file_deleted: boolean }>(`/api/books/${bookId}`, {
    method: 'DELETE'
  })
}

export function fetchLocalFiles() {
  return request<LocalFile[]>('/api/local-files')
}

export function importLocalBook(path: string) {
  return request<{ book_id: number; title: string; author: string }>('/api/books/import-local', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path })
  })
}

export async function uploadBook(file: File) {
  const form = new FormData()
  form.append('file', file)
  return request<{ book_id: number; title: string; author: string }>('/api/books/upload', {
    method: 'POST',
    body: form
  })
}

export async function uploadAndProcessBook(file: File) {
  const uploaded = await uploadBook(file)
  await request(`/api/books/${uploaded.book_id}/process`, { method: 'POST' })
  return uploaded
}

export function processBook(bookId: number) {
  return request(`/api/books/${bookId}/process`, { method: 'POST' })
}

export function fetchProcessProgress(bookId: number) {
  return request<ProcessProgress>(`/api/books/${bookId}/process-progress`)
}

export function fetchChapters(bookId: number) {
  return request<ChapterSummary[]>(`/api/books/${bookId}/chapters`)
}

export function fetchChapter(chapterId: number) {
  return request<Chapter>(`/api/chapters/${chapterId}`)
}

export function fetchPlaces(bookId: number) {
  return request<PlaceCount[]>(`/api/books/${bookId}/places`)
}

export function correctMention(
  mentionId: number,
  payload: { canonical_name: string; lat: number; lng: number; save_alias: boolean }
) {
  return request(`/api/mentions/${mentionId}/correct`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
}

export function deleteMention(mentionId: number) {
  return request(`/api/mentions/${mentionId}`, {
    method: 'DELETE'
  })
}

export type CandidateSearchResult = {
  raw_name: string
  status: string
  place: {
    place_id: number
    canonical_name: string
    lat: number
    lng: number
    source: string
  } | null
  mentions_created: number
  context_level: string
  llm_result: Record<string, unknown>
}

export function searchPlaceCandidate(payload: {
  book_id: number
  paragraph_id: number
  raw_name: string
  start_offset: number
  end_offset: number
}) {
  return request<CandidateSearchResult>('/api/place-candidates/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
}

export function ignorePlaceCandidate(payload: { book_id: number; raw_name: string }) {
  return request('/api/place-candidates/ignore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
}
