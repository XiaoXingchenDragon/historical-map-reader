'use client'

import dynamic from 'next/dynamic'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { MouseEvent as ReactMouseEvent, memo, useCallback, useEffect, useMemo, useState } from 'react'
import {
  Chapter,
  ChapterSummary,
  Mention,
  Paragraph,
  PlaceCandidate,
  PlaceCount,
  correctMention,
  deleteMention,
  fetchChapter,
  fetchChapters,
  fetchPlaces,
  ignorePlaceCandidate,
  processBook,
  searchPlaceCandidate
} from '@/lib/api'

const MapPanel = dynamic(() => import('@/components/MapPanel'), { ssr: false })

type TextSegment = {
  text: string
  mention?: Mention
  candidate?: PlaceCandidate
}

type SelectionAction = {
  candidate: PlaceCandidate
  top: number
  left: number
}

type ParagraphViewProps = {
  paragraph: Paragraph
  focused: boolean
  firstMentionIds: Set<number>
  mentionOccurrenceRanks: Map<number, number>
  activeMentionId: number | null
  activeCandidateId: string | null
  candidateBusy: boolean
  mentionDeleteBusy: boolean
  onMentionClick: (mention: Mention, paragraphId: number) => void
  onCandidateClick: (candidate: PlaceCandidate, paragraphId: number) => void
  onCandidateSearch: (candidate: PlaceCandidate) => void
  onCandidateDelete: () => void
  onMentionDelete: () => void
}

function buildSegments(paragraph: Paragraph): TextSegment[] {
  const items = [
    ...(paragraph.mentions || []).map((mention) => ({ type: 'mention' as const, ...mention })),
    ...(paragraph.candidates || []).map((candidate) => ({ type: 'candidate' as const, ...candidate }))
  ].sort((a, b) => a.start_offset - b.start_offset || b.end_offset - a.end_offset)

  const segments: TextSegment[] = []
  let cursor = 0
  for (const item of items) {
    if (item.start_offset < cursor) continue
    if (item.start_offset > cursor) {
      segments.push({ text: paragraph.text.slice(cursor, item.start_offset) })
    }
    const text = paragraph.text.slice(item.start_offset, item.end_offset)
    if (item.type === 'mention') {
      segments.push({ text, mention: item })
    } else {
      segments.push({ text, candidate: item })
    }
    cursor = item.end_offset
  }
  if (cursor < paragraph.text.length) {
    segments.push({ text: paragraph.text.slice(cursor) })
  }
  return segments
}

function mentionKey(mention: Mention) {
  if (mention.place_id) return `place:${mention.place_id}`
  if (mention.canonical_name) return `name:${mention.canonical_name.toLocaleLowerCase()}`
  return `raw:${mention.raw_name.toLocaleLowerCase()}`
}

function uniqueMentionsForChapter(chapter: Chapter | null) {
  if (!chapter) return []
  const seen = new Set<string>()
  const visibleMentions: Mention[] = []

  chapter.paragraphs.forEach((paragraph) => {
    paragraph.mentions.forEach((mention) => {
      const key = mentionKey(mention)
      if (seen.has(key)) return
      seen.add(key)
      visibleMentions.push({ ...mention, paragraph_id: paragraph.paragraph_id })
    })
  })

  return visibleMentions
}

function mentionOccurrenceRanksForChapter(chapter: Chapter | null) {
  const ranks = new Map<number, number>()
  if (!chapter) return ranks
  const counts = new Map<string, number>()

  chapter.paragraphs.forEach((paragraph) => {
    paragraph.mentions.forEach((mention) => {
      const key = mentionKey(mention)
      const nextCount = (counts.get(key) || 0) + 1
      counts.set(key, nextCount)
      ranks.set(mention.id, nextCount)
    })
  })

  return ranks
}

function paragraphHasMention(paragraph: Paragraph, mentionId: number | null) {
  return mentionId !== null && paragraph.mentions.some((mention) => mention.id === mentionId)
}

function paragraphHasCandidate(paragraph: Paragraph, candidateId: string | null) {
  return candidateId !== null && paragraph.candidates.some((candidate) => candidate.id === candidateId)
}

const ParagraphView = memo(function ParagraphView({
  paragraph,
  focused,
  firstMentionIds,
  mentionOccurrenceRanks,
  activeMentionId,
  activeCandidateId,
  candidateBusy,
  mentionDeleteBusy,
  onMentionClick,
  onCandidateClick,
  onCandidateSearch,
  onCandidateDelete,
  onMentionDelete
}: ParagraphViewProps) {
  const segments = useMemo(() => buildSegments(paragraph), [paragraph])

  return (
    <p
      id={`paragraph-${paragraph.paragraph_id}`}
      data-paragraph-id={paragraph.paragraph_id}
      className={`paragraph ${focused ? 'focused' : ''}`}
    >
      {segments.map((segment, index) => {
        if (segment.mention) {
          const mention = segment.mention
          const isActiveMention = activeMentionId === mention.id
          const occurrenceRank = mentionOccurrenceRanks.get(mention.id) || 1
          return (
            <span
              key={index}
              className={[
                'mention',
                !mention.lat || !mention.lng ? 'unmapped' : '',
                firstMentionIds.has(mention.id) ? '' : 'duplicate',
                occurrenceRank > 3 ? 'repeat-muted' : '',
                isActiveMention ? 'active' : ''
              ].join(' ')}
              onClick={(event) => {
                event.stopPropagation()
                onMentionClick(mention, paragraph.paragraph_id)
              }}
            >
              {segment.text}
              {isActiveMention ? (
                <span className="candidate-inline-actions" onClick={(event) => event.stopPropagation()}>
                  <button
                    type="button"
                    title="Delete mention"
                    disabled={mentionDeleteBusy}
                    onClick={onMentionDelete}
                  >
                    删
                  </button>
                </span>
              ) : null}
            </span>
          )
        }
        if (segment.candidate) {
          const candidate = segment.candidate
          const isActiveCandidate = activeCandidateId === candidate.id
          return (
            <span
              key={index}
              className={`place-candidate ${isActiveCandidate ? 'active' : ''}`}
              onClick={(event) => {
                event.stopPropagation()
                onCandidateClick(candidate, paragraph.paragraph_id)
              }}
            >
              {segment.text}
              {isActiveCandidate ? (
                <span className="candidate-inline-actions" onClick={(event) => event.stopPropagation()}>
                  <button
                    type="button"
                    title="Search"
                    disabled={candidateBusy}
                    onClick={() => onCandidateSearch(candidate)}
                  >
                    搜
                  </button>
                  <button
                    type="button"
                    title="Delete"
                    disabled={candidateBusy}
                    onClick={onCandidateDelete}
                  >
                    删
                  </button>
                </span>
              ) : null}
            </span>
          )
        }
        return <span key={index}>{segment.text}</span>
      })}
    </p>
  )
}, areParagraphViewPropsEqual)

function areParagraphViewPropsEqual(prev: ParagraphViewProps, next: ParagraphViewProps) {
  if (prev.paragraph !== next.paragraph) return false
  if (prev.focused !== next.focused) return false
  if (prev.firstMentionIds !== next.firstMentionIds) return false
  if (prev.mentionOccurrenceRanks !== next.mentionOccurrenceRanks) return false
  if (prev.activeMentionId !== next.activeMentionId) return false
  if (prev.activeCandidateId !== next.activeCandidateId) return false
  if ((prev.activeMentionId || next.activeMentionId) && prev.mentionDeleteBusy !== next.mentionDeleteBusy) return false
  if ((prev.activeCandidateId || next.activeCandidateId) && prev.candidateBusy !== next.candidateBusy) return false
  return true
}

export default function BookPage() {
  const params = useParams<{ bookId: string }>()
  const bookId = Number(params?.bookId || 0)
  const [chapters, setChapters] = useState<ChapterSummary[]>([])
  const [currentChapter, setCurrentChapter] = useState<Chapter | null>(null)
  const [places, setPlaces] = useState<PlaceCount[]>([])
  const [selectedMentionId, setSelectedMentionId] = useState<number | null>(null)
  const [focusedParagraphId, setFocusedParagraphId] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [correction, setCorrection] = useState({
    mention: null as Mention | null,
    candidate: null as PlaceCandidate | null,
    canonical_name: '',
    lat: '',
    lng: '',
    category: '',
    save_alias: true
  })
  const [candidateStatus, setCandidateStatus] = useState('')
  const [candidateBusy, setCandidateBusy] = useState(false)
  const [mentionDeleteBusy, setMentionDeleteBusy] = useState(false)
  const [activeMentionActionId, setActiveMentionActionId] = useState<number | null>(null)
  const [selectionAction, setSelectionAction] = useState<SelectionAction | null>(null)
  const [selectedPlaceInfo, setSelectedPlaceInfo] = useState<{
    raw_name: string
    canonical_name: string
  } | null>(null)

  const loadChapter = useCallback(async (chapterId: number) => {
    const chapter = await fetchChapter(chapterId)
    setCurrentChapter(chapter)
    setSelectedMentionId(null)
    setActiveMentionActionId(null)
    setSelectionAction(null)
  }, [])

  const reloadBook = useCallback(async () => {
    if (!bookId) return
    const [chapterList, placeList] = await Promise.all([fetchChapters(bookId), fetchPlaces(bookId)])
    setChapters(chapterList)
    setPlaces(placeList)
    if (chapterList.length > 0) {
      await loadChapter(chapterList[0].id)
    } else {
      setCurrentChapter(null)
    }
  }, [bookId, loadChapter])

  useEffect(() => {
    async function loadBook() {
      if (!bookId) return
      setLoading(true)
      setError('')
      try {
        await reloadBook()
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : 'Load failed.')
      } finally {
        setLoading(false)
      }
    }
    loadBook()
  }, [bookId, reloadBook])

  async function reprocessCurrentBook() {
    if (!bookId) return
    setLoading(true)
    setError('')
    try {
      await processBook(bookId)
      await reloadBook()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Reprocess failed.')
    } finally {
      setLoading(false)
    }
  }

  const uniqueMentions = useMemo(() => uniqueMentionsForChapter(currentChapter), [currentChapter])
  const firstMentionIds = useMemo(() => new Set(uniqueMentions.map((mention) => mention.id)), [uniqueMentions])
  const firstMentionIdByKey = useMemo(() => {
    const byKey = new Map<string, number>()
    uniqueMentions.forEach((mention) => byKey.set(mentionKey(mention), mention.id))
    return byKey
  }, [uniqueMentions])
  const mentionOccurrenceRanks = useMemo(() => mentionOccurrenceRanksForChapter(currentChapter), [currentChapter])

  function resetCorrection() {
    setCorrection({
      mention: null,
      candidate: null,
      canonical_name: '',
      lat: '',
      lng: '',
      category: '',
      save_alias: true
    })
    setCandidateStatus('')
    setCandidateBusy(false)
    setActiveMentionActionId(null)
    setSelectionAction(null)
    setSelectedPlaceInfo(null)
  }

  const openMentionCorrection = useCallback((mention: Mention) => {
    setCandidateStatus('')
    setSelectedPlaceInfo(null)
    setCorrection({
      mention,
      candidate: null,
      canonical_name: mention.canonical_name || mention.raw_name,
      lat: mention.lat?.toString() || '',
      lng: mention.lng?.toString() || '',
      category: '',
      save_alias: true
    })
  }, [])

  const openCandidateCorrection = useCallback((candidate: PlaceCandidate, paragraphId: number) => {
    if (correction.candidate?.id === candidate.id) {
      resetCorrection()
      return
    }
    setFocusedParagraphId(paragraphId)
    setCandidateStatus('')
    setSelectedPlaceInfo(null)
    setSelectionAction(null)
    setActiveMentionActionId(null)
    setCorrection({
      mention: null,
      candidate,
      canonical_name: candidate.raw_name,
      lat: '',
      lng: '',
      category: '',
      save_alias: true
    })
  }, [correction.candidate?.id])

  const selectMention = useCallback((mention: Mention, paragraphId: number) => {
    if (activeMentionActionId === mention.id) {
      resetCorrection()
      return
    }
    setSelectedMentionId(firstMentionIdByKey.get(mentionKey(mention)) || mention.id)
    setFocusedParagraphId(paragraphId)
    setActiveMentionActionId(mention.id)
    setSelectionAction(null)
    openMentionCorrection(mention)
  }, [activeMentionActionId, firstMentionIdByKey, openMentionCorrection])

  function scrollToParagraph(paragraphId: number) {
    setFocusedParagraphId(paragraphId)
    document.getElementById(`paragraph-${paragraphId}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }

  const onMarkerClick = useCallback((mention: Mention) => {
    setSelectedMentionId(mention.id)
    if (mention.paragraph_id) scrollToParagraph(mention.paragraph_id)
    setActiveMentionActionId(mention.id)
    setSelectionAction(null)
    openMentionCorrection(mention)
  }, [openMentionCorrection])

  async function saveCorrection() {
    if (!currentChapter || !correction.mention) return
    const lat = Number(correction.lat)
    const lng = Number(correction.lng)
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
      setError('Please enter valid latitude and longitude.')
      return
    }

    await correctMention(correction.mention.id, {
      canonical_name: correction.canonical_name || correction.mention.raw_name,
      lat,
      lng,
      save_alias: correction.save_alias
    })

    setCurrentChapter(await fetchChapter(currentChapter.id))
    setPlaces(await fetchPlaces(bookId))
    resetCorrection()
  }

  const correctionTitle = correction.mention?.raw_name || correction.candidate?.raw_name || ''

  async function deleteCandidate() {
    if (!currentChapter || !correction.candidate) return
    setCandidateBusy(true)
    setError('')
    try {
      await ignorePlaceCandidate({ book_id: bookId, raw_name: correction.candidate.raw_name })
      setCurrentChapter(await fetchChapter(currentChapter.id))
      resetCorrection()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Delete candidate failed.')
      setCandidateBusy(false)
    }
  }

  async function deleteSelectedMention() {
    if (!currentChapter || !correction.mention) return
    setMentionDeleteBusy(true)
    setError('')
    try {
      await deleteMention(correction.mention.id)
      setCurrentChapter(await fetchChapter(currentChapter.id))
      setPlaces(await fetchPlaces(bookId))
      resetCorrection()
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Delete mention failed.'
      setError(
        message === 'Method Not Allowed' || message === 'Not Found'
          ? '后端尚未提供地点批注删除接口：需要 DELETE /api/mentions/{mention_id} 删除或隐藏单条误识别批注。'
          : message
      )
    } finally {
      setMentionDeleteBusy(false)
    }
  }

  async function searchCandidate(targetCandidate = correction.candidate) {
    if (!currentChapter || !targetCandidate) return
    setCandidateBusy(true)
    setError('')
    setCandidateStatus('Searching...')
    try {
      const result = await searchPlaceCandidate({
        book_id: bookId,
        paragraph_id: targetCandidate.paragraph_id,
        raw_name: targetCandidate.raw_name,
        start_offset: targetCandidate.start_offset,
        end_offset: targetCandidate.end_offset
      })
      if (result.place) {
        const nextChapter = await fetchChapter(currentChapter.id)
        const nextMentions = uniqueMentionsForChapter(nextChapter)
        const resolvedMention = nextMentions.find(
          (mention) =>
            mention.place_id === result.place?.place_id ||
            (mention.raw_name === targetCandidate.raw_name &&
              mention.canonical_name === result.place?.canonical_name)
        )

        setCurrentChapter(nextChapter)
        setPlaces(await fetchPlaces(bookId))
        setCorrection({
          mention: null,
          candidate: null,
          canonical_name: '',
          lat: '',
          lng: '',
          category: '',
          save_alias: true
        })
        setCandidateStatus('')
        setSelectionAction(null)
        setSelectedPlaceInfo({
          raw_name: targetCandidate.raw_name,
          canonical_name: result.place.canonical_name
        })
        if (resolvedMention) {
          setSelectedMentionId(resolvedMention.id)
          if (resolvedMention.paragraph_id) setFocusedParagraphId(resolvedMention.paragraph_id)
        }
      } else {
        setCandidateStatus(`Not resolved: ${result.status}`)
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Search candidate failed.')
      setCandidateStatus('')
    } finally {
      setCandidateBusy(false)
    }
  }

  function openSelectionCandidate(event: ReactMouseEvent<HTMLElement>) {
    const readerElement = event.currentTarget
    setTimeout(() => {
      const selection = window.getSelection()
      if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
        setSelectionAction(null)
        return
      }

      const range = selection.getRangeAt(0)
      const paragraphElement =
        (range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
          ? (range.commonAncestorContainer as Element)
          : range.commonAncestorContainer.parentElement
        )?.closest<HTMLParagraphElement>('.paragraph')

      if (
        !paragraphElement ||
        !readerElement.contains(paragraphElement) ||
        !paragraphElement.contains(range.startContainer) ||
        !paragraphElement.contains(range.endContainer)
      ) {
        setSelectionAction(null)
        return
      }

      const paragraphId = Number(paragraphElement.dataset.paragraphId)
      const paragraph = currentChapter?.paragraphs.find((item) => item.paragraph_id === paragraphId)
      if (!paragraph) return

      const beforeRange = document.createRange()
      beforeRange.selectNodeContents(paragraphElement)
      beforeRange.setEnd(range.startContainer, range.startOffset)
      let startOffset = beforeRange.toString().length
      let endOffset = startOffset + range.toString().length

      const selectedText = paragraph.text.slice(startOffset, endOffset)
      const leadingSpaces = selectedText.match(/^\s*/)?.[0].length || 0
      const trailingSpaces = selectedText.match(/\s*$/)?.[0].length || 0
      startOffset += leadingSpaces
      endOffset -= trailingSpaces
      const rawName = paragraph.text.slice(startOffset, endOffset)
      if (!rawName.trim()) {
        setSelectionAction(null)
        return
      }

      const rect = range.getBoundingClientRect()
      resetCorrection()
      setSelectionAction({
        candidate: {
          id: `manual:${paragraphId}:${startOffset}:${endOffset}`,
          paragraph_id: paragraphId,
          raw_name: rawName,
          start_offset: startOffset,
          end_offset: endOffset,
          reason: 'manual-selection'
        },
        top: Math.max(8, rect.top - 34),
        left: Math.min(window.innerWidth - 40, rect.right + 4)
      })
    }, 0)
  }

  return (
    <main
      className="shell"
      onClick={() => {
        const selection = window.getSelection()
        if (selection && !selection.isCollapsed) return
        if (correction.candidate || activeMentionActionId !== null || selectionAction) resetCorrection()
      }}
    >
      <header className="topbar">
        <Link href="/">
          <button className="secondary">Back</button>
        </Link>
        <div className="brand">{currentChapter?.title || 'Reader'}</div>
        <button className="secondary" disabled={loading} onClick={reprocessCurrentBook}>
          Reprocess
        </button>
        <span className="muted">{uniqueMentions.length} markers / {places.length} places</span>
      </header>

      <section className="reader-layout">
        <div className="reader-pane">
          <aside className="chapter-list">
            {chapters.map((chapter) => (
              <button
                key={chapter.id}
                className={`chapter-button ${currentChapter?.id === chapter.id ? 'active' : ''}`}
                onClick={() => loadChapter(chapter.id)}
              >
                {chapter.title || `Chapter ${chapter.order_index + 1}`}
              </button>
            ))}
          </aside>

          <article className="chapter-content" onMouseUp={openSelectionCandidate}>
            <h1 className="chapter-title">{currentChapter?.title}</h1>
            {loading ? <p className="muted">Loading...</p> : null}
            {error ? <p className="muted">{error}</p> : null}
            {currentChapter?.paragraphs.map((paragraph) => {
              const activeMentionIdForParagraph = paragraphHasMention(paragraph, activeMentionActionId)
                ? activeMentionActionId
                : null
              const activeCandidateId = correction.candidate?.id || null
              const activeCandidateIdForParagraph = paragraphHasCandidate(paragraph, activeCandidateId)
                ? activeCandidateId
                : null

              return (
                <ParagraphView
                  key={paragraph.paragraph_id}
                  paragraph={paragraph}
                  focused={focusedParagraphId === paragraph.paragraph_id}
                  firstMentionIds={firstMentionIds}
                  mentionOccurrenceRanks={mentionOccurrenceRanks}
                  activeMentionId={activeMentionIdForParagraph}
                  activeCandidateId={activeCandidateIdForParagraph}
                  candidateBusy={candidateBusy}
                  mentionDeleteBusy={mentionDeleteBusy}
                  onMentionClick={selectMention}
                  onCandidateClick={openCandidateCorrection}
                  onCandidateSearch={searchCandidate}
                  onCandidateDelete={deleteCandidate}
                  onMentionDelete={deleteSelectedMention}
                />
              )
            })}
          </article>
        </div>

        <div className="map-pane">
          <MapPanel
            mentions={uniqueMentions}
            selectedMentionId={selectedMentionId}
            fitKey={currentChapter?.id || null}
            onMarkerClick={onMarkerClick}
          />

          {correction.mention ? (
            <form
              className="correction-panel"
              onSubmit={(event) => {
                event.preventDefault()
                saveCorrection()
              }}
            >
              <strong>{correction.candidate ? 'Resolve candidate' : 'Correct place'}: {correctionTitle}</strong>
              <label className="correction-check">
                <input
                  type="checkbox"
                  checked={correction.save_alias}
                  onChange={(event) => setCorrection((value) => ({ ...value, save_alias: event.target.checked }))}
                />
                Save raw text as alias
              </label>
              <div className="correction-grid candidate-grid">
                <input
                  value={correction.canonical_name}
                  placeholder="Place name"
                  onChange={(event) => setCorrection((value) => ({ ...value, canonical_name: event.target.value }))}
                />
                <input
                  value={correction.lat}
                  placeholder="lat"
                  onChange={(event) => setCorrection((value) => ({ ...value, lat: event.target.value }))}
                />
                <input
                  value={correction.lng}
                  placeholder="lng"
                  onChange={(event) => setCorrection((value) => ({ ...value, lng: event.target.value }))}
                />
                <input
                  value={correction.category}
                  placeholder="category"
                  onChange={(event) => setCorrection((value) => ({ ...value, category: event.target.value }))}
                />
                <button type="submit">Save</button>
              </div>
            </form>
          ) : null}

          {!correction.mention && !correction.candidate && selectedPlaceInfo ? (
            <section className="place-info-panel">
              <span>{selectedPlaceInfo.raw_name}</span>
              <strong>{selectedPlaceInfo.canonical_name}</strong>
            </section>
          ) : null}
        </div>
      </section>

      {selectionAction ? (
        <span
          className="selection-inline-actions"
          style={{ top: selectionAction.top, left: selectionAction.left }}
          onClick={(event) => event.stopPropagation()}
        >
          <button
            type="button"
            title="Search selected place"
            disabled={candidateBusy}
            onClick={() => searchCandidate(selectionAction.candidate)}
          >
            搜
          </button>
        </span>
      ) : null}
    </main>
  )
}
