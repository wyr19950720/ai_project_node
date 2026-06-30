// server/src/services/rag/ingest.js
// 文档入库：上传 → 读取文本 → 分片 → 向量化 → 存入内存向量库
import fs from 'fs/promises'
import path from 'path'
import { RecursiveCharacterTextSplitter } from '@langchain/textsplitters'
import { embeddings } from '../model.js'
import { logger } from '../../utils/logger.js'

// 单次入库最多 300 个 chunk，防止 embedding API 调用过多
const MAX_CHUNKS = 300

// ── 轻量内存向量库（余弦相似度） ──────────────────────────────
class MemoryVectorStore {
  constructor() {
    // [{ content, embedding, metadata }]
    this.memoryVectors = []
  }

  async addDocuments(documents) {
    const texts = documents.map(d => d.pageContent)
    // 分批调用 embedding，每批 20 条，避免并发过多
    const BATCH = 20
    for (let i = 0; i < texts.length; i += BATCH) {
      const batch     = texts.slice(i, i + BATCH)
      const batchDocs = documents.slice(i, i + BATCH)
      const vectors   = await embeddings.embedDocuments(batch)
      for (let j = 0; j < batch.length; j++) {
        this.memoryVectors.push({
          content:   batch[j],
          embedding: vectors[j],
          metadata:  batchDocs[j].metadata,
        })
      }
      logger.info('rag: embedding progress', {
        done: Math.min(i + BATCH, texts.length),
        total: texts.length,
      })
    }
  }

  async similaritySearchWithScore(query, k = 4, filter) {
    const queryVec = await embeddings.embedQuery(query)

    let pool = this.memoryVectors
    if (typeof filter === 'function') {
      pool = pool.filter(v =>
        filter({ pageContent: v.content, metadata: v.metadata })
      )
    }

    const scored = pool.map(v => ({
      doc:   { pageContent: v.content, metadata: v.metadata },
      score: cosineSim(queryVec, v.embedding),
    }))
    scored.sort((a, b) => b.score - a.score)

    return scored.slice(0, k).map(({ doc, score }) => [doc, score])
  }
}

function cosineSim(a, b) {
  let dot = 0, na = 0, nb = 0
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i]
    na  += a[i] * a[i]
    nb  += b[i] * b[i]
  }
  const denom = Math.sqrt(na) * Math.sqrt(nb)
  return denom === 0 ? 0 : dot / denom
}

// ── 向量库单例 ─────────────────────────────────────────────────
let vectorStore = null

export async function getVectorStore() {
  if (vectorStore) return vectorStore

  if (!embeddings) {
    throw new Error('未配置 ZHIPU_API_KEY，无法使用 RAG 功能')
  }

  vectorStore = new MemoryVectorStore()
  logger.info('rag: memory vector store initialized')
  return vectorStore
}

// ── 文档元数据注册表 ──────────────────────────────────────────
const docRegistry = new Map()

export function getDocRegistry() {
  return [...docRegistry.values()]
}

export function getDoc(docId) {
  return docRegistry.get(docId) || null
}

// ── 文本提取：根据文件类型读取内容 ───────────────────────────
async function extractText(filePath) {
  const ext = path.extname(filePath).toLowerCase()

  if (ext === '.txt' || ext === '.md') {
    return fs.readFile(filePath, 'utf-8')
  }

  if (ext === '.pdf') {
    // pdfjs-dist v5 需要 DOMMatrix，Node.js 低版本没有，在此注入 polyfill
    if (typeof globalThis.DOMMatrix === 'undefined') {
      globalThis.DOMMatrix = class DOMMatrix {
        constructor(init) {
          this.a = 1; this.b = 0; this.c = 0; this.d = 1; this.e = 0; this.f = 0
          this.m11 = 1; this.m12 = 0; this.m13 = 0; this.m14 = 0
          this.m21 = 0; this.m22 = 1; this.m23 = 0; this.m24 = 0
          this.m31 = 0; this.m32 = 0; this.m33 = 1; this.m34 = 0
          this.m41 = 0; this.m42 = 0; this.m43 = 0; this.m44 = 1
          this.is2D = true; this.isIdentity = true
          if (Array.isArray(init) && init.length === 6) {
            [this.a, this.b, this.c, this.d, this.e, this.f] = init
            this.m11 = this.a; this.m12 = this.b
            this.m21 = this.c; this.m22 = this.d
            this.m41 = this.e; this.m42 = this.f
          }
        }
        multiply(o) {
          const m = new globalThis.DOMMatrix()
          m.a = this.a * o.a + this.c * o.b
          m.b = this.b * o.a + this.d * o.b
          m.c = this.a * o.c + this.c * o.d
          m.d = this.b * o.c + this.d * o.d
          m.e = this.a * o.e + this.c * o.f + this.e
          m.f = this.b * o.e + this.d * o.f + this.f
          return m
        }
        inverse() {
          const det = this.a * this.d - this.b * this.c
          const m = new globalThis.DOMMatrix()
          if (det === 0) return m
          m.a =  this.d / det;  m.b = -this.b / det
          m.c = -this.c / det;  m.d =  this.a / det
          m.e = (this.c * this.f - this.d * this.e) / det
          m.f = (this.b * this.e - this.a * this.f) / det
          return m
        }
        transformPoint(p) {
          return { x: this.a * p.x + this.c * p.y + this.e, y: this.b * p.x + this.d * p.y + this.f }
        }
        scale(sx, sy = sx) {
          const m = new globalThis.DOMMatrix()
          m.a = sx; m.d = sy
          return this.multiply(m)
        }
        translate(tx, ty) {
          const m = new globalThis.DOMMatrix()
          m.e = tx; m.f = ty
          return this.multiply(m)
        }
      }
    }
    try {
      const { PDFParse } = await import('pdf-parse')
      const buffer = await fs.readFile(filePath)
      const parser = new PDFParse({ data: buffer })
      await parser.load()
      const result = await parser.getText()
      return result.text
    } catch (e) {
      logger.warn('pdf-parse failed', { error: e.message })
      throw new Error(`PDF 解析失败：${e.message}`)
    }
  }

  return fs.readFile(filePath, 'utf-8')
}

// ── 核心：文档入库 ─────────────────────────────────────────────
export async function ingestDocument({ filePath, fileName, title, category = '通用' }) {
  const docId = `doc_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`

  logger.info('rag: ingesting document', { docId, title, category })

  // 1. 提取文本
  const rawText = await extractText(filePath)
  if (!rawText.trim()) {
    throw new Error('文档内容为空，无法处理')
  }

  // 2. 文档分片
  const splitter = new RecursiveCharacterTextSplitter({
    chunkSize:    500,
    chunkOverlap: 50,
    separators: ['\n\n', '\n', '。', '；', '，', ' ', ''],
  })

  let chunks = await splitter.createDocuments(
    [rawText],
    [{ docId, title: title || fileName, category, fileName, uploadedAt: new Date().toISOString() }]
  )

  if (chunks.length > MAX_CHUNKS) {
    logger.warn('rag: too many chunks, truncating', {
      original: chunks.length, truncated: MAX_CHUNKS,
    })
    chunks = chunks.slice(0, MAX_CHUNKS)
  }

  logger.info('rag: document split', { docId, chunks: chunks.length })

  // 3. 向量化并存入内存向量库
  const vs = await getVectorStore()
  await vs.addDocuments(chunks)

  // 4. 注册文档元数据
  const docMeta = {
    id:         docId,
    title:      title || fileName,
    fileName,
    category,
    chunks:     chunks.length,
    chars:      rawText.length,
    uploadedAt: new Date().toISOString(),
    preview:    rawText.slice(0, 120).replace(/\n/g, ' ') + '...',
  }
  docRegistry.set(docId, docMeta)

  // 5. 清理临时文件
  await fs.unlink(filePath).catch(() => {})

  logger.info('rag: ingest complete', { docId, chunks: chunks.length })
  return docMeta
}

// ── 删除文档 ──────────────────────────────────────────────────
export async function deleteDocument(docId) {
  const doc = docRegistry.get(docId)
  if (!doc) throw new Error('文档不存在')

  if (vectorStore) {
    vectorStore.memoryVectors = vectorStore.memoryVectors.filter(
      v => v.metadata.docId !== docId
    )
  }

  docRegistry.delete(docId)
  logger.info('rag: document deleted', { docId })
}
