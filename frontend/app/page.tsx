'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { ChangeEvent, useEffect, useRef, useState } from 'react'
import { Book, deleteBook, fetchBooks, processBook, uploadBook } from '@/lib/api'

function nextPaint() {
  return new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
  })
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
    setStatus(file ? `已选择：${file.name}` : '')
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
    setStatus('正在上传并解析...')
    await nextPaint()

    try {
      const uploaded = await uploadBook(file)
      setStatus('文件已上传，正在解析...')
      await nextPaint()
      await processBook(uploaded.book_id)
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
            {loading ? '正在上传并解析...' : '上传并解析'}
          </button>
          <span className="muted">{status}</span>
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
