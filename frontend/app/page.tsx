'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { ChangeEvent, useEffect, useRef, useState } from 'react'
import { Book, ProcessProgress, deleteBook, fetchBooks, fetchProcessProgress, processBook, uploadBook } from '@/lib/api'

function nextPaint() {
  return new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
  })
}

const initialProgress: ProcessProgress = {
  book_id: 0,
  stage: 'idle',
  percent: 0,
  current: 0,
  total: 0,
  detail: ''
}

export default function HomePage() {
  const router = useRouter()
  const fileInput = useRef<HTMLInputElement | null>(null)
  const selectedFileRef = useRef<File | null>(null)
  const busyRef = useRef(false)
  const [books, setBooks] = useState<Book[]>([])
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [progress, setProgress] = useState<ProcessProgress>(initialProgress)

  useEffect(() => {
    fetchBooks().then(setBooks).catch((reason) => {
      setError(`无法连接后端：${reason.message}`)
    })
  }, [])

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] || null
    selectedFileRef.current = file
    setSelectedFile(file)
    setError('')
    setProgress(initialProgress)
    setStatus(file ? `已选择：${file.name}` : '')
  }

  async function pollProgress(bookId: number, processPromise: Promise<unknown>) {
    let finished = false
    processPromise.finally(() => {
      finished = true
    })

    while (!finished) {
      try {
        const nextProgress = await fetchProcessProgress(bookId)
        setProgress(nextProgress)
        setStatus(nextProgress.detail || '正在解析...')
      } catch {
        setStatus('正在解析...')
      }
      await new Promise((resolve) => setTimeout(resolve, 350))
    }

    const finalProgress = await fetchProcessProgress(bookId).catch(() => null)
    if (finalProgress) setProgress(finalProgress)
  }

  async function upload() {
    if (busyRef.current) return
    const file = selectedFileRef.current || selectedFile || fileInput.current?.files?.[0] || null
    if (!file) {
      setError('请先选择 EPUB 或 PDF 文件。')
      setStatus('')
      return
    }

    busyRef.current = true
    setLoading(true)
    setError('')
    setProgress({ ...initialProgress, stage: 'uploading', percent: 2, detail: '正在上传文件' })
    setStatus('正在上传文件...')
    await nextPaint()

    try {
      const uploaded = await uploadBook(file)
      setProgress({ book_id: uploaded.book_id, stage: 'queued', percent: 5, current: 0, total: 0, detail: '文件已上传，准备解析' })
      setStatus('文件已上传，准备解析...')
      await nextPaint()

      const processPromise = processBook(uploaded.book_id)
      await Promise.all([processPromise, pollProgress(uploaded.book_id, processPromise)])
      setProgress((value) => ({ ...value, stage: 'done', percent: 100 }))
      setStatus('解析完成，正在打开...')
      await nextPaint()
      router.push(`/books/${uploaded.book_id}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '上传失败。')
    } finally {
      busyRef.current = false
      setLoading(false)
    }
  }

  async function removeBook(book: Book) {
    if (!window.confirm(`删除《${book.title}》及其解析数据？`)) return
    setError('')
    try {
      await deleteBook(book.id)
      setBooks((currentBooks) => currentBooks.filter((item) => item.id !== book.id))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '删除失败。')
    }
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">历史书智能地图阅读器 MVP</div>
        <span className="muted">Local EPUB/PDF reader</span>
      </header>

      <section className="home">
        <h1>上传 EPUB/PDF 并生成地点地图</h1>

        <div className="upload-row">
          <input
            ref={fileInput}
            className="file-input"
            type="file"
            accept=".epub,.pdf,application/epub+zip,application/pdf"
            onChange={onFileChange}
          />
          <button type="button" disabled={loading} onClick={upload}>
            {loading ? '处理中...' : '上传并解析'}
          </button>
          <span className="muted">{status}</span>
          {loading ? (
            <div className="upload-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress.percent}>
              <div className="upload-progress-bar" style={{ width: `${progress.percent}%` }} />
              <div className="upload-progress-meta">
                <span>{progress.percent}%</span>
                <span>{progress.total ? `${progress.current}/${progress.total} 段` : progress.stage}</span>
              </div>
            </div>
          ) : null}
        </div>

        {error ? <p className="muted">{error}</p> : null}

        <h2>已上传书籍</h2>
        <div className="book-list">
          {books.map((book) => (
            <div key={book.id} className="book-item">
              <div>
                <strong>{book.title}</strong>
                <div className="muted">{book.author}</div>
              </div>
              <div className="book-actions">
                <button className="secondary" onClick={() => removeBook(book)}>
                  删除
                </button>
                <Link href={`/books/${book.id}`}>
                  <button>打开</button>
                </Link>
              </div>
            </div>
          ))}
          {books.length === 0 ? <p className="muted">还没有上传的书籍。</p> : null}
        </div>
      </section>
    </main>
  )
}
