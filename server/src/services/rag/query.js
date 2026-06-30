// server/src/services/rag/query.js
// RAG 查询：检索相关文档 + 生成有来源标注的回答
import { ChatPromptTemplate } from '@langchain/core/prompts'
import { StringOutputParser } from '@langchain/core/output_parsers'
import { RunnablePassthrough, RunnableSequence } from '@langchain/core/runnables'
import { chatModel } from '../model.js'
import { getVectorStore } from './ingest.js'
import { logger } from '../../utils/logger.js'

// ── 相似度阈值：低于此值的文档不纳入参考 ─────────────────────
// 0 = 完全无关，1 = 完全相同
// 0.3 是经验值：太高会漏掉相关文档，太低引入噪音
const SIMILARITY_THRESHOLD = 0.3

// ── 检索相关文档 ───────────────────────────────────────────────
/**
 * @param {string} question  - 用户问题
 * @param {object} options
 * @param {string}   options.category  - 可选：只在指定分类里搜索
 * @param {number}   options.k         - 返回文档数量（默认4）
 */
export async function retrieveDocs(question, { category, k = 4 } = {}) {
  const vs = await getVectorStore()

  // MemoryVectorStore 的 filter 是函数形式
  const filter = category
    ? (doc) => doc.metadata.category === category
    : undefined

  const results = await vs.similaritySearchWithScore(question, k, filter)

  // 过滤掉相似度太低的结果
  const relevant = results.filter(([, score]) => score > SIMILARITY_THRESHOLD)

  logger.info('rag: retrieved docs', {
    question: question.slice(0, 40),
    total: results.length,
    relevant: relevant.length,
    topScore: results[0]?.[1]?.toFixed(3),
  })

  return relevant.map(([doc, score]) => ({
    content:  doc.pageContent,
    score:    parseFloat(score.toFixed(3)),
    title:    doc.metadata.title || '未知来源',
    docId:    doc.metadata.docId,
    category: doc.metadata.category,
    // 展示给用户的简短预览
    preview:  doc.pageContent.slice(0, 80).replace(/\n/g, ' ') + '...',
  }))
}

// ── RAG Prompt 模板 ───────────────────────────────────────────
const RAG_SYSTEM = `你是 WorkMind AI 知识库助手。

规则：
1. 只根据下方提供的参考文档回答问题，不使用文档之外的知识
2. 如果文档中没有相关内容，明确说"知识库中未找到相关内容"
3. 回答要准确、简洁，必要时列出要点
4. 在回答末尾用 【来源：文档名】 标注使用了哪些文档`

// ── 非流式 RAG（适合短问题）────────────────────────────────────
export async function ragQuery(question, options = {}) {
  const docs = await retrieveDocs(question, options)

  if (!docs.length) {
    return {
      answer:  '知识库中未找到与该问题相关的内容。请尝试换一种提问方式，或上传相关文档后再试。',
      sources: [],
    }
  }

  // 把检索到的文档格式化成参考资料
  const context = docs
    .map((doc, i) => `[参考${i + 1}] 来源：${doc.title}\n${doc.content}`)
    .join('\n\n---\n\n')

  const prompt = ChatPromptTemplate.fromMessages([
    ['system', RAG_SYSTEM],
    ['human', `参考文档：\n{context}\n\n问题：{question}`],
  ])

  const chain = prompt.pipe(chatModel).pipe(new StringOutputParser())
  const answer = await chain.invoke({ context, question })

  return { answer, sources: docs }
}

// ── 流式 RAG：边生成边推送 ─────────────────────────────────────
// 先推送检索到的来源，再流式推送回答内容
export async function ragQueryStream(question, options = {}) {
  const docs = await retrieveDocs(question, options)

  // 返回一个异步生成器，外层路由负责推 SSE
  return {
    sources: docs,
    // streamAnswer() 是一个异步生成器函数
    async *streamAnswer() {
      if (!docs.length) {
        yield '知识库中未找到与该问题相关的内容。\n请尝试换一种提问方式，或上传相关文档后再试。'
        return
      }

      const context = docs
        .map((doc, i) => `[参考${i + 1}] 来源：${doc.title}\n${doc.content}`)
        .join('\n\n---\n\n')

      const prompt = ChatPromptTemplate.fromMessages([
        ['system', RAG_SYSTEM],
        ['human', `参考文档：\n{context}\n\n问题：{question}`],
      ])

      // 使用 streaming 模型
      const streamingModel = chatModel  // chatModel 已开启 streaming
      const chain = prompt.pipe(streamingModel)

      const stream = await chain.stream({ context, question })

      for await (const chunk of stream) {
        if (chunk.content) yield chunk.content
      }
    },
  }
}
