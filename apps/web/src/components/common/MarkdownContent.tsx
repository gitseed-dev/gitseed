import React from 'react'
import { Highlight, themes } from 'prism-react-renderer'
import { useTheme } from '../../context/ThemeContext'
import { normalizeCodeLanguage } from '../../utils/codeLanguage'

export interface MarkdownContentProps {
  content: string
  className?: string
}

export function renderInline(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = []
  const tokenRegex =
    /(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|\*[^*\n]+\*|_[^_\n]+_|(?<![\w/])@[a-z][a-z0-9-]{1,31}\b)/g

  let lastIndex = 0
  let match: RegExpExecArray | null
  let keyIdx = 0

  while ((match = tokenRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index))
    }
    const token = match[0]
    keyIdx += 1

    if (token.startsWith('`') && token.endsWith('`')) {
      nodes.push(
        <code
          key={keyIdx}
          className="px-1.5 py-0.5 rounded-md bg-zinc-200/70 dark:bg-zinc-700/70 font-mono text-[0.85em] text-teal-700 dark:text-teal-300 wrap-break-word break-all sm:break-normal"
        >
          {token.slice(1, -1)}
        </code>
      )
    } else if (
      (token.startsWith('**') && token.endsWith('**')) ||
      (token.startsWith('__') && token.endsWith('__'))
    ) {
      nodes.push(
        <strong
          key={keyIdx}
          className="font-semibold text-zinc-900 dark:text-zinc-100 wrap-break-word"
        >
          {renderInline(token.slice(2, -2))}
        </strong>
      )
    } else if (match[2] && match[3]) {
      nodes.push(
        <a
          key={keyIdx}
          href={match[3]}
          target="_blank"
          rel="noopener noreferrer"
          className="text-teal-600 dark:text-teal-400 underline underline-offset-2 hover:opacity-80 break-all"
        >
          {match[2]}
        </a>
      )
    } else if (token.startsWith('@')) {
      nodes.push(
        <span
          key={keyIdx}
          className="px-1.5 py-0.5 rounded-md bg-teal-500/10 text-teal-700 dark:text-teal-300 font-mono text-[0.88em] font-semibold"
        >
          {token}
        </span>
      )
    } else if (
      (token.startsWith('*') && token.endsWith('*')) ||
      (token.startsWith('_') && token.endsWith('_'))
    ) {
      nodes.push(
        <em key={keyIdx} className="italic">
          {renderInline(token.slice(1, -1))}
        </em>
      )
    } else {
      nodes.push(token)
    }

    lastIndex = tokenRegex.lastIndex
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex))
  }

  return nodes
}

export const MarkdownContent: React.FC<MarkdownContentProps> = ({
  content,
  className = '',
}) => {
  const { theme } = useTheme()
  const lines = content.replace(/\r\n/g, '\n').split('\n')
  const blocks: React.ReactNode[] = []
  let i = 0
  let blockKey = 0

  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()

    if (!trimmed) {
      i += 1
      continue
    }

    // Fenced code block
    if (trimmed.startsWith('```')) {
      const lang = trimmed.slice(3).trim()
      const codeLines: string[] = []
      i += 1
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        codeLines.push(lines[i])
        i += 1
      }
      if (i < lines.length) {
        i += 1
      }
      blockKey += 1
      const normalizedLang = normalizeCodeLanguage(lang)
      blocks.push(
        <div
          key={blockKey}
          className="max-w-full min-w-0 rounded-xl overflow-hidden border border-zinc-200 dark:border-zinc-700/80 bg-zinc-50 dark:bg-zinc-950 text-zinc-800 dark:text-zinc-100 my-1"
        >
          {lang && (
            <div className="px-3 py-1 text-[11px] font-mono uppercase tracking-wider text-zinc-500 dark:text-zinc-400 bg-zinc-100 dark:bg-zinc-900/90 border-b border-zinc-200 dark:border-zinc-800 truncate">
              {lang}
            </div>
          )}
          <Highlight
            code={codeLines.join('\n')}
            language={normalizedLang}
            theme={theme === 'dark' ? themes.oneDark : themes.oneLight}
          >
            {({ tokens, getLineProps, getTokenProps }) => (
              <pre className="p-3.5 text-xs font-mono overflow-x-auto max-w-full leading-relaxed">
                <code>
                  {tokens.map((line, lineIdx) => (
                    <div
                      key={lineIdx}
                      {...getLineProps({ line })}
                      style={undefined}
                    >
                      {line.map((token, tokenIdx) => (
                        <span key={tokenIdx} {...getTokenProps({ token })} />
                      ))}
                    </div>
                  ))}
                </code>
              </pre>
            )}
          </Highlight>
        </div>
      )
      continue
    }

    // Horizontal rule
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      blockKey += 1
      blocks.push(
        <hr
          key={blockKey}
          className="border-t border-zinc-200 dark:border-zinc-700/80 my-2"
        />
      )
      i += 1
      continue
    }

    // Headings
    const headingMatch = /^(#{1,6})\s+(.+)$/.exec(trimmed)
    if (headingMatch) {
      const level = headingMatch[1].length
      const headingText = headingMatch[2]
      blockKey += 1
      const sizeClass =
        level === 1
          ? 'text-base sm:text-lg font-bold text-zinc-900 dark:text-zinc-100 mt-2 first:mt-0'
          : level === 2
            ? 'text-sm sm:text-base font-bold text-zinc-900 dark:text-zinc-100 mt-2 first:mt-0'
            : 'text-xs sm:text-sm font-semibold text-zinc-900 dark:text-zinc-100 mt-1.5 first:mt-0'
      blocks.push(
        <div key={blockKey} className={`font-heading wrap-break-word min-w-0 ${sizeClass}`}>
          {renderInline(headingText)}
        </div>
      )
      i += 1
      continue
    }

    // Blockquote
    if (trimmed.startsWith('>')) {
      const quoteLines: string[] = []
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        quoteLines.push(lines[i].trim().replace(/^>\s?/, ''))
        i += 1
      }
      blockKey += 1
      blocks.push(
        <blockquote
          key={blockKey}
          className="pl-3.5 py-1 border-l-2 border-teal-500/60 text-zinc-600 dark:text-zinc-300 italic text-xs sm:text-sm wrap-break-word min-w-0"
        >
          {renderInline(quoteLines.join(' '))}
        </blockquote>
      )
      continue
    }

    // Table
    if (
      trimmed.includes('|') &&
      i + 1 < lines.length &&
      /^\s*\|?[\s:-]+\|[\s|:-]*$/.test(lines[i + 1])
    ) {
      const parseRow = (raw: string) =>
        raw
          .trim()
          .replace(/^\|/, '')
          .replace(/\|$/, '')
          .split('|')
          .map((cell) => cell.trim())

      const headers = parseRow(lines[i])
      i += 2
      const rows: string[][] = []
      while (i < lines.length && lines[i].trim().includes('|')) {
        rows.push(parseRow(lines[i]))
        i += 1
      }
      blockKey += 1
      blocks.push(
        <div
          key={blockKey}
          className="max-w-full min-w-0 overflow-x-auto rounded-xl border border-zinc-200 dark:border-zinc-700/80 my-1"
        >
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-zinc-100/80 dark:bg-zinc-800/80 border-b border-zinc-200 dark:border-zinc-700/80">
                {headers.map((h, idx) => (
                  <th
                    key={idx}
                    className="px-3 py-2 font-semibold text-zinc-900 dark:text-zinc-100 wrap-break-word"
                  >
                    {renderInline(h)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-200/70 dark:divide-zinc-800">
              {rows.map((r, rIdx) => (
                <tr key={rIdx}>
                  {r.map((cell, cIdx) => (
                    <td
                      key={cIdx}
                      className="px-3 py-2 text-zinc-700 dark:text-zinc-300 wrap-break-word"
                    >
                      {renderInline(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
      continue
    }

    // Unordered list
    if (/^[-*+]\s+/.test(trimmed)) {
      const items: string[] = []
      while (i < lines.length && /^[-*+]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^[-*+]\s+/, ''))
        i += 1
      }
      blockKey += 1
      blocks.push(
        <ul
          key={blockKey}
          className="list-disc pl-5 space-y-1 text-xs sm:text-sm text-zinc-800 dark:text-zinc-200 leading-relaxed min-w-0"
        >
          {items.map((item, idx) => (
            <li key={idx} className="wrap-break-word">{renderInline(item)}</li>
          ))}
        </ul>
      )
      continue
    }

    // Ordered list
    if (/^\d+\.\s+/.test(trimmed)) {
      const items: string[] = []
      while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^\d+\.\s+/, ''))
        i += 1
      }
      blockKey += 1
      blocks.push(
        <ol
          key={blockKey}
          className="list-decimal pl-5 space-y-1 text-xs sm:text-sm text-zinc-800 dark:text-zinc-200 leading-relaxed min-w-0"
        >
          {items.map((item, idx) => (
            <li key={idx} className="wrap-break-word">{renderInline(item)}</li>
          ))}
        </ol>
      )
      continue
    }

    // Paragraph
    const paraLines: string[] = [trimmed]
    i += 1
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !lines[i].trim().startsWith('```') &&
      !lines[i].trim().startsWith('#') &&
      !lines[i].trim().startsWith('>') &&
      !/^[-*+]\s+/.test(lines[i].trim()) &&
      !/^\d+\.\s+/.test(lines[i].trim()) &&
      !/^(-{3,}|\*{3,}|_{3,})$/.test(lines[i].trim())
    ) {
      paraLines.push(lines[i].trim())
      i += 1
    }
    blockKey += 1
    blocks.push(
      <p
        key={blockKey}
        className="text-xs sm:text-sm text-zinc-800 dark:text-zinc-200 leading-relaxed whitespace-pre-wrap wrap-break-word min-w-0"
      >
        {renderInline(paraLines.join('\n'))}
      </p>
    )
  }

  return <div className={`flex flex-col gap-2.5 min-w-0 max-w-full wrap-break-word ${className}`}>{blocks}</div>
}

