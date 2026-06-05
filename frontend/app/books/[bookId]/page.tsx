'use client'

import dynamic from 'next/dynamic'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import {
  MouseEvent as ReactMouseEvent,
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState
} from 'react'
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

const DEFAULT_FONT_SIZE = 17
const MIN_FONT_SIZE = 14
const MAX_FONT_SIZE = 26

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

type PageBlock = {
  paragraph: Paragraph
  startOffset: number
  endOffset: number
}

type ParagraphViewProps = {
  paragraph: Paragraph
  originalParagraphId?: number
  offsetStart?: number
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

function clampInlineItems<T extends Mention | PlaceCandidate>(items: T[], startOffset: number, endOffset: number): T[] {
  return items
    .filter((item) => item.start_offset >= startOffset && item.end_offset <= endOffset)
    .map((item) => ({
      ...item,
      start_offset: item.start_offset - startOffset,
      end_offset: item.end_offset - startOffset
    }))
}

function paragraphBlock(paragraph: Paragraph, startOffset = 0, endOffset = paragraph.text.length): PageBlock {
  return { paragraph, startOffset, endOffset }
}

function sliceParagraphForBlock(block: PageBlock): Paragraph {
  if (block.startOffset === 0 && block.endOffset === block.paragraph.text.length) return block.paragraph
  return {
    ...block.paragraph,
    text: block.paragraph.text.slice(block.startOffset, block.endOffset),
    mentions: clampInlineItems(block.paragraph.mentions, block.startOffset, block.endOffset),
    candidates: clampInlineItems(block.paragraph.candidates, block.startOffset, block.endOffset)
  }
}

function findChunkEnd(text: string, startOffset: number, targetOffset: number, minEnd: number) {
  const safeTarget = Math.min(text.length, Math.max(minEnd, targetOffset))
  const windowStart = Math.max(minEnd, safeTarget - 80)
  const windowEnd = Math.min(text.length, safeTarget + 80)
  const punctuation = /[。！？；.!?;]\s*/g
  let bestEnd = safeTarget
  let match: RegExpExecArray | null
  punctuation.lastIndex = windowStart
  while ((match = punctuation.exec(text)) && match.index <= windowEnd) {
    const candidateEnd = match.index + match[0].length
    if (candidateEnd >= minEnd) bestEnd = candidateEnd
  }
  if (bestEnd <= startOffset) bestEnd = Math.min(text.length, minEnd)
  return bestEnd
}

function splitLargeParagraph(paragraph: Paragraph, paragraphHeight: number, pageHeight: number): PageBlock[] {
  if (paragraphHeight <= pageHeight || paragraph.text.length <= 80) return [paragraphBlock(paragraph)]
  const chunkCount = Math.max(2, Math.ceil(paragraphHeight / Math.max(1, pageHeight * 0.62)))
  const targetLength = Math.max(80, Math.ceil(paragraph.text.length / chunkCount))
  const blocks: PageBlock[] = []
  let startOffset = 0

  while (startOffset < paragraph.text.length) {
    const minEnd = Math.min(paragraph.text.length, startOffset + Math.max(60, Math.floor(targetLength * 0.62)))
    const targetEnd = Math.min(paragraph.text.length, startOffset + targetLength)
    const endOffset =
      targetEnd >= paragraph.text.length
        ? paragraph.text.length
        : findChunkEnd(paragraph.text, startOffset, targetEnd, minEnd)
    blocks.push(paragraphBlock(paragraph, startOffset, endOffset))
    startOffset = endOffset
  }

  return blocks
}

function fallbackParagraphHeight(paragraph: Paragraph, width: number, fontSize: number) {
  const lineHeight = fontSize * 1.85
  const charsPerLine = Math.max(8, Math.floor(width / fontSize))
  return Math.max(lineHeight, Math.ceil(paragraph.text.length / charsPerLine) * lineHeight + fontSize * 1.1)
}

function paginateParagraphs(
  paragraphs: Paragraph[],
  width: number,
  height: number,
  fontSize: number,
  measuredHeights: Map<number, number>
) {
  if (!paragraphs.length) return [[]] as PageBlock[][]
  const pageHeight = Math.max(fontSize * 8, height) * 0.82
  const pages: PageBlock[][] = []
  let currentPage: PageBlock[] = []
  let currentHeight = 0

  for (const paragraph of paragraphs) {
    const paragraphHeight =
      measuredHeights.get(paragraph.paragraph_id) || fallbackParagraphHeight(paragraph, width, fontSize)
    const blocks = splitLargeParagraph(paragraph, paragraphHeight, pageHeight)

    for (const block of blocks) {
      const blockRatio = (block.endOffset - block.startOffset) / Math.max(1, paragraph.text.length)
      const blockHeight = Math.min(paragraphHeight, Math.max(fontSize * 2.95, paragraphHeight * blockRatio))
      if (currentPage.length > 0 && currentHeight + blockHeight > pageHeight) {
        pages.push(currentPage)
        currentPage = []
        currentHeight = 0
      }
      currentPage.push(block)
      currentHeight += blockHeight
    }
  }

  if (currentPage.length > 0) pages.push(currentPage)
  return pages
}

function pageIndexForParagraph(pages: PageBlock[][], paragraphId: number) {
  const index = pages.findIndex((page) => page.some((block) => block.paragraph.paragraph_id === paragraphId))
  return index >= 0 ? index : null
}

const ParagraphView = memo(function ParagraphView({
  paragraph,
  originalParagraphId,
  offsetStart = 0,
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
      id={`paragraph-${originalParagraphId || paragraph.paragraph_id}`}
      data-paragraph-id={originalParagraphId || paragraph.paragraph_id}
      data-paragraph-offset={offsetStart}
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
          const actionCandidate =
            offsetStart > 0
              ? {
                  ...candidate,
                  start_offset: candidate.start_offset + offsetStart,
                  end_offset: candidate.end_offset + offsetStart
                }
              : candidate
          const isActiveCandidate = activeCandidateId === candidate.id
          return (
            <span
              key={index}
              className={`place-candidate ${isActiveCandidate ? 'active' : ''}`}
              onClick={(event) => {
                event.stopPropagation()
                onCandidateClick(actionCandidate, originalParagraphId || paragraph.paragraph_id)
              }}
            >
              {segment.text}
              {isActiveCandidate ? (
                <span className="candidate-inline-actions" onClick={(event) => event.stopPropagation()}>
                  <button
                    type="button"
                    title="Search"
                    disabled={candidateBusy}
                    onClick={() => onCandidateSearch(actionCandidate)}
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
  if (prev.originalParagraphId !== next.originalParagraphId) return false
  if (prev.offsetStart !== next.offsetStart) return false
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
  const pageTextRef = useRef<HTMLDivElement | null>(null)
  const measureRef = useRef<HTMLDivElement | null>(null)
  const [chapters, setChapters] = useState<ChapterSummary[]>([])
  const [currentChapter, setCurrentChapter] = useState<Chapter | null>(null)
  const [places, setPlaces] = useState<PlaceCount[]>([])
  const [selectedMentionId, setSelectedMentionId] = useState<number | null>(null)
  const [focusedParagraphId, setFocusedParagraphId] = useState<number | null>(null)
  const [pageIndex, setPageIndex] = useState(0)
  const [fontSize, setFontSize] = useState(DEFAULT_FONT_SIZE)
  const [pageBox, setPageBox] = useState({ width: 640, height: 640 })
  const [measuredHeights, setMeasuredHeights] = useState<Map<number, number>>(() => new Map())
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

  const loadChapter = useCallback(async (chapterId: number, targetPage: 'first' | 'last' = 'first') => {
    const chapter = await fetchChapter(chapterId)
    setCurrentChapter(chapter)
    setPageIndex(targetPage === 'last' ? Number.MAX_SAFE_INTEGER : 0)
    setSelectedMentionId(null)
    setFocusedParagraphId(null)
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
  const chapterIndex = useMemo(
    () => chapters.findIndex((chapter) => chapter.id === currentChapter?.id),
    [chapters, currentChapter?.id]
  )
  const pages = useMemo(
    () => paginateParagraphs(currentChapter?.paragraphs || [], pageBox.width, pageBox.height, fontSize, measuredHeights),
    [currentChapter, fontSize, measuredHeights, pageBox.height, pageBox.width]
  )
  const currentPageIndex = Math.min(Math.max(pageIndex, 0), Math.max(0, pages.length - 1))
  const visibleParagraphs = pages[currentPageIndex] || []
  const canGoToPreviousChapter = chapterIndex > 0
  const canGoToNextChapter = chapterIndex >= 0 && chapterIndex < chapters.length - 1
  const canGoPrevious = currentPageIndex > 0 || canGoToPreviousChapter
  const canGoNext = currentPageIndex < pages.length - 1 || canGoToNextChapter

  useEffect(() => {
    const element = pageTextRef.current
    if (!element) return

    function updatePageBox() {
      if (!element) return
      setPageBox({
        width: Math.max(240, element.clientWidth),
        height: Math.max(240, element.clientHeight)
      })
    }

    updatePageBox()
    const observer = new ResizeObserver(updatePageBox)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    setPageIndex((value) => Math.min(Math.max(value, 0), Math.max(0, pages.length - 1)))
  }, [pages.length])

  useLayoutEffect(() => {
    const measureElement = measureRef.current
    if (!measureElement || !currentChapter) {
      setMeasuredHeights(new Map())
      return
    }

    const nextHeights = new Map<number, number>()
    measureElement.querySelectorAll<HTMLElement>('[data-measure-paragraph-id]').forEach((element) => {
      const paragraphId = Number(element.dataset.measureParagraphId)
      if (Number.isFinite(paragraphId)) {
        nextHeights.set(paragraphId, element.getBoundingClientRect().height)
      }
    })
    setMeasuredHeights(nextHeights)
  }, [currentChapter, fontSize, pageBox.width])

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

  function goToPreviousPage() {
    if (currentPageIndex > 0) {
      setPageIndex((value) => Math.max(0, value - 1))
      resetCorrection()
      return
    }
    goToPreviousChapter()
  }

  function goToNextPage() {
    if (currentPageIndex < pages.length - 1) {
      setPageIndex((value) => Math.min(pages.length - 1, value + 1))
      resetCorrection()
      return
    }
    goToNextChapter()
  }

  function goToPreviousChapter() {
    if (!canGoToPreviousChapter) return
    loadChapter(chapters[chapterIndex - 1].id, 'last')
    resetCorrection()
  }

  function goToNextChapter() {
    if (!canGoToNextChapter) return
    loadChapter(chapters[chapterIndex + 1].id, 'first')
    resetCorrection()
  }

  function adjustFontSize(delta: number) {
    setFontSize((value) => Math.min(MAX_FONT_SIZE, Math.max(MIN_FONT_SIZE, value + delta)))
  }

  function scrollToParagraph(paragraphId: number) {
    setFocusedParagraphId(paragraphId)
    const targetPageIndex = pageIndexForParagraph(pages, paragraphId)
    if (targetPageIndex !== null) setPageIndex(targetPageIndex)
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
      const paragraphOffset = Number(paragraphElement.dataset.paragraphOffset || 0)
      const paragraph = currentChapter?.paragraphs.find((item) => item.paragraph_id === paragraphId)
      if (!paragraph) return

      const beforeRange = document.createRange()
      beforeRange.selectNodeContents(paragraphElement)
      beforeRange.setEnd(range.startContainer, range.startOffset)
      let startOffset = paragraphOffset + beforeRange.toString().length
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

          <article className="chapter-content paged-reader">
            <div className="reader-tools" onClick={(event) => event.stopPropagation()}>
              <button
                type="button"
                className="icon-button"
                title="减小字号"
                disabled={fontSize <= MIN_FONT_SIZE}
                onClick={() => adjustFontSize(-1)}
              >
                🔍-
              </button>
              <button
                type="button"
                className="icon-button"
                title="增大字号"
                disabled={fontSize >= MAX_FONT_SIZE}
                onClick={() => adjustFontSize(1)}
              >
                🔍+
              </button>
            </div>

            <button
              type="button"
              className="page-turn-zone page-turn-zone-left"
              disabled={!canGoPrevious}
              onClick={(event) => {
                event.stopPropagation()
                goToPreviousPage()
              }}
              aria-label="上一页"
            >
              &lt;
            </button>
            <button
              type="button"
              className="page-turn-zone page-turn-zone-right"
              disabled={!canGoNext}
              onClick={(event) => {
                event.stopPropagation()
                goToNextPage()
              }}
              aria-label="下一页"
            >
              &gt;
            </button>

            <div className="page-shell">
              <h1 className="chapter-title">{currentChapter?.title}</h1>
              {loading ? <p className="muted">Loading...</p> : null}
              {error ? <p className="muted">{error}</p> : null}
              <div
                ref={pageTextRef}
                className="page-text"
                style={{ fontSize }}
                onMouseUp={openSelectionCandidate}
              >
                {visibleParagraphs.map((block) => {
                  const paragraph = sliceParagraphForBlock(block)
                  const originalParagraph = block.paragraph
                  const activeMentionIdForParagraph = paragraphHasMention(originalParagraph, activeMentionActionId)
                    ? activeMentionActionId
                    : null
                  const activeCandidateId = correction.candidate?.id || null
                  const activeCandidateIdForParagraph = paragraphHasCandidate(originalParagraph, activeCandidateId)
                    ? activeCandidateId
                    : null

                  return (
                    <ParagraphView
                      key={`${originalParagraph.paragraph_id}:${block.startOffset}:${block.endOffset}`}
                      paragraph={paragraph}
                      originalParagraphId={originalParagraph.paragraph_id}
                      offsetStart={block.startOffset}
                      focused={focusedParagraphId === originalParagraph.paragraph_id}
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
              </div>

              <div
                ref={measureRef}
                className="page-measure"
                style={{ fontSize, width: pageBox.width }}
                aria-hidden="true"
              >
                {currentChapter?.paragraphs.map((paragraph) => (
                  <p
                    key={paragraph.paragraph_id}
                    className="paragraph"
                    data-measure-paragraph-id={paragraph.paragraph_id}
                  >
                    {paragraph.text}
                  </p>
                ))}
              </div>

              <footer className="page-footer" onClick={(event) => event.stopPropagation()}>
                <button type="button" className="secondary" disabled={!canGoPrevious} onClick={goToPreviousPage}>
                  上一页
                </button>
                <button
                  type="button"
                  className="secondary"
                  disabled={!canGoToPreviousChapter}
                  onClick={goToPreviousChapter}
                >
                  上一章
                </button>
                <span className="muted">
                  {pages.length > 0 ? `${currentPageIndex + 1} / ${pages.length}` : '0 / 0'}
                </span>
                <button
                  type="button"
                  className="secondary"
                  disabled={!canGoToNextChapter}
                  onClick={goToNextChapter}
                >
                  下一章
                </button>
                <button type="button" className="secondary" disabled={!canGoNext} onClick={goToNextPage}>
                  下一页
                </button>
              </footer>
            </div>
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
