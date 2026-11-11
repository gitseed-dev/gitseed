export type ParsedRunLogEntryKind =
  | 'system'
  | 'thinking'
  | 'tool'
  | 'message'
  | 'stage_output'
  | 'error'

export type ToolCallCategory =
  | 'read'
  | 'edit'
  | 'bash'
  | 'search'
  | 'git'
  | 'other'

export interface ParsedRunLogEntry {
  id: string
  kind: ParsedRunLogEntryKind
  timestamp?: string
  title: string
  subtitle?: string
  detail?: string
  toolName?: string
  toolCategory?: ToolCallCategory
  toolStatus?: 'running' | 'ok' | 'error'
}

function classifyToolCategory(toolName: string): ToolCallCategory {
  const lower = toolName.toLowerCase()
  if (
    lower.includes('read') ||
    lower.includes('view') ||
    lower.includes('cat') ||
    lower.includes('ls') ||
    lower.includes('list_dir')
  ) {
    return 'read'
  }
  if (
    lower.includes('edit') ||
    lower.includes('write') ||
    lower.includes('patch') ||
    lower.includes('replace') ||
    lower.includes('create_file') ||
    lower.includes('file_change') ||
    lower.includes('multiedit')
  ) {
    return 'edit'
  }
  if (
    lower.includes('bash') ||
    lower.includes('cmd') ||
    lower.includes('command') ||
    lower.includes('shell') ||
    lower.includes('exec')
  ) {
    return 'bash'
  }
  if (
    lower.includes('grep') ||
    lower.includes('glob') ||
    lower.includes('find') ||
    lower.includes('search')
  ) {
    return 'search'
  }
  if (lower.includes('git')) {
    return 'git'
  }
  return 'other'
}

function extractToolTargetAndDetail(rawArgs: unknown): {
  target: string
  detail?: string
} {
  if (!rawArgs) return { target: '' }

  if (typeof rawArgs === 'string') {
    const trimmed = rawArgs.trim()
    if (!trimmed) return { target: '' }
    if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
      try {
        const parsed = JSON.parse(trimmed) as Record<string, unknown>
        return extractToolTargetAndDetail(parsed)
      } catch {
        // Fall through to key=value or raw string handling
      }
    }
    const kvMatch = trimmed.match(
      /^(?:path|file_path|Path|target_file|command|CommandLine|cmd|pattern|query|url)=(.+)$/
    )
    if (kvMatch) {
      return { target: kvMatch[1].trim() }
    }
    return {
      target: trimmed.length > 120 ? `${trimmed.slice(0, 120)}…` : trimmed,
      detail: trimmed.length > 120 ? trimmed : undefined,
    }
  }

  if (typeof rawArgs === 'object' && rawArgs !== null) {
    const obj = rawArgs as Record<string, unknown>
    const priorityKeys = [
      'file_path',
      'path',
      'Path',
      'target_file',
      'command',
      'CommandLine',
      'cmd',
      'pattern',
      'query',
      'url',
      'description',
    ]
    let target = ''
    for (const k of priorityKeys) {
      const val = obj[k]
      if (typeof val === 'string' && val.trim()) {
        target = val.trim().replace(/\s+/g, ' ')
        break
      }
    }
    if (!target) {
      const firstPairs = Object.entries(obj)
        .filter(([, v]) => v !== null && v !== undefined && String(v).trim() !== '')
        .slice(0, 2)
        .map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
      target = firstPairs.join(' ')
    }
    const prettyJson = JSON.stringify(obj, null, 2)
    return {
      target: target.length > 120 ? `${target.slice(0, 120)}…` : target,
      detail: Object.keys(obj).length > 0 ? prettyJson : undefined,
    }
  }

  return { target: String(rawArgs) }
}

function extractResultText(rawOutput: unknown): string {
  if (!rawOutput) return ''
  if (typeof rawOutput === 'string') {
    return rawOutput.trim()
  }
  if (Array.isArray(rawOutput)) {
    return rawOutput
      .map((item) => {
        if (typeof item === 'string') return item.trim()
        if (item && typeof item === 'object' && 'text' in item) {
          return String((item as Record<string, unknown>).text || '').trim()
        }
        return JSON.stringify(item)
      })
      .filter(Boolean)
      .join('\n')
      .trim()
  }
  if (typeof rawOutput === 'object') {
    const obj = rawOutput as Record<string, unknown>
    for (const k of ['output', 'stdout', 'result', 'content', 'text', 'error']) {
      if (typeof obj[k] === 'string' && (obj[k] as string).trim()) {
        return (obj[k] as string).trim()
      }
    }
    return JSON.stringify(obj, null, 2)
  }
  return String(rawOutput)
}

function tryFormatStageOutputObject(
  obj: Record<string, unknown>,
  nextId: () => string
): ParsedRunLogEntry | null {
  const isStagePayload =
    'summary_comment' in obj ||
    'specification' in obj ||
    'acceptance_criteria' in obj ||
    ('plan' in obj && 'tasks' in obj) ||
    ('approved' in obj && 'findings' in obj)
  if (!isStagePayload) return null

  const summary =
    typeof obj.summary_comment === 'string' && obj.summary_comment.trim()
      ? obj.summary_comment.trim()
      : typeof obj.specification === 'string' && obj.specification.trim()
        ? 'Drafted technical specification and acceptance criteria.'
        : typeof obj.plan === 'string' && obj.plan.trim()
          ? 'Generated implementation plan and execution tasks.'
          : 'Stage output payload recorded.'

  return {
    id: nextId(),
    kind: 'stage_output',
    title: 'Stage Output Synthesized',
    subtitle: summary,
    detail: JSON.stringify(obj, null, 2),
  }
}

export function parseRunLogStream(
  rawLog: string,
  isRunActive: boolean
): ParsedRunLogEntry[] {
  if (!rawLog || !rawLog.trim()) return []

  const entries: ParsedRunLogEntry[] = []
  let seq = 0
  const nextId = () => `log-step-${++seq}`

  const attachOrAddToolResult = (
    toolLabel: string,
    status: 'ok' | 'error',
    outputText: string
  ) => {
    const trimmedOut = outputText.trim()
    const oneLineOut = trimmedOut.replace(/\s+/g, ' ').slice(0, 140)
    // Find the most recent unresolved tool entry
    for (let i = entries.length - 1; i >= Math.max(0, entries.length - 6); i--) {
      const candidate = entries[i]
      if (candidate.kind === 'tool' && candidate.toolStatus === 'running') {
        candidate.toolStatus = status
        if (trimmedOut) {
          candidate.detail = candidate.detail
            ? `${candidate.detail}\n\n--- Output ---\n${trimmedOut}`
            : trimmedOut
          if (!candidate.subtitle) {
            candidate.subtitle = oneLineOut
          }
        }
        return
      }
    }
    entries.push({
      id: nextId(),
      kind: 'tool',
      toolName: toolLabel || 'tool',
      toolCategory: classifyToolCategory(toolLabel || 'tool'),
      toolStatus: status,
      title: toolLabel || 'tool',
      subtitle: oneLineOut || undefined,
      detail: trimmedOut || undefined,
    })
  }

  const pushTextMessage = (rawText: string) => {
    const cleaned = rawText.trim()
    if (!cleaned) return

    // Check if the text is a fenced or bare stage output JSON object
    const fenceMatch = cleaned.match(/^```(?:json)?\s*(\{[\s\S]*\})\s*```$/)
    const jsonCandidate = fenceMatch ? fenceMatch[1].trim() : cleaned
    if (jsonCandidate.startsWith('{') && jsonCandidate.endsWith('}')) {
      try {
        const parsedObj = JSON.parse(jsonCandidate) as Record<string, unknown>
        if (parsedObj && typeof parsedObj === 'object') {
          const stageEntry = tryFormatStageOutputObject(parsedObj, nextId)
          if (stageEntry) {
            entries.push(stageEntry)
            return
          }
        }
      } catch {
        // Not valid JSON; treat as regular message
      }
    }

    const last = entries[entries.length - 1]
    if (last && last.kind === 'message') {
      last.title = `${last.title}\n${cleaned}`
      return
    }
    entries.push({
      id: nextId(),
      kind: 'message',
      title: cleaned,
    })
  }

  const processJsonEvent = (ev: Record<string, unknown>) => {
    const stageEntry = tryFormatStageOutputObject(ev, nextId)
    if (stageEntry && !ev.type && !ev.event) {
      entries.push(stageEntry)
      return
    }

    const evType = String(ev.type || ev.event || '').toLowerCase()
    const msgDict =
      ev.message && typeof ev.message === 'object'
        ? (ev.message as Record<string, unknown>)
        : null
    const role = String(
      ev.role || (msgDict ? msgDict.role : '') || ''
    ).toLowerCase()

    if (
      evType === 'ping' ||
      evType === 'rate_limit_event' ||
      evType === 'step_start' ||
      evType === 'step_finish' ||
      evType === 'thread.started' ||
      evType === 'turn.started' ||
      evType === 'turn.completed'
    ) {
      return
    }

    if (evType === 'init' || evType === 'session' || evType === 'system') {
      const subtype = String(ev.subtype || '').toLowerCase()
      if (subtype && subtype !== 'init' && subtype !== 'session') return
      const model = String(ev.model || '').trim()
      entries.push({
        id: nextId(),
        kind: 'system',
        title: `Agent session initialized${model ? ` (${model})` : ''}`,
      })
      return
    }

    if (
      evType === 'item.started' ||
      evType === 'item.completed' ||
      evType === 'item.updated'
    ) {
      const item =
        ev.item && typeof ev.item === 'object'
          ? (ev.item as Record<string, unknown>)
          : null
      if (!item) return
      const itype = String(item.type || '').toLowerCase()
      if (itype === 'reasoning' || itype === 'thinking') {
        const thought = String(item.text || item.summary || '').trim()
        if (thought && evType === 'item.completed') {
          entries.push({
            id: nextId(),
            kind: 'thinking',
            title: thought,
          })
        }
        return
      }
      if (itype === 'command_execution') {
        const cmdStr = String(item.command || '').trim()
        if (evType === 'item.started') {
          entries.push({
            id: nextId(),
            kind: 'tool',
            toolName: 'bash',
            toolCategory: 'bash',
            toolStatus: 'running',
            title: 'bash',
            subtitle: cmdStr,
            detail: cmdStr ? `$ ${cmdStr}` : undefined,
          })
        } else if (evType === 'item.completed') {
          const exitCode = item.exit_code
          const status =
            typeof exitCode === 'number' && exitCode !== 0 ? 'error' : 'ok'
          const outText = extractResultText(
            item.aggregated_output || item.output
          )
          attachOrAddToolResult('bash', status, outText)
        }
        return
      }
      if (itype === 'file_change' && evType === 'item.completed') {
        const changes = Array.isArray(item.changes) ? item.changes : []
        const paths = changes
          .map((c) =>
            c && typeof c === 'object'
              ? String((c as Record<string, unknown>).path || '')
              : ''
          )
          .filter(Boolean)
          .join(', ')
        entries.push({
          id: nextId(),
          kind: 'tool',
          toolName: 'file_change',
          toolCategory: 'edit',
          toolStatus: 'ok',
          title: 'file_change',
          subtitle: paths || 'Updated workspace files',
        })
        return
      }
      if (
        (itype === 'agent_message' || itype === 'message') &&
        evType === 'item.completed'
      ) {
        pushTextMessage(String(item.text || item.content || ''))
      }
      return
    }

    if (evType === 'thinking' || evType === 'thought' || 'thinking' in ev) {
      const thought = String(
        ev.thinking || ev.thought || ev.content || ''
      ).trim()
      if (thought) {
        entries.push({
          id: nextId(),
          kind: 'thinking',
          title: thought,
        })
      }
      return
    }

    if (
      evType === 'tool_use' ||
      evType === 'tool_call' ||
      evType === 'function_call' ||
      evType === 'functioncall'
    ) {
      const tName = String(ev.name || ev.tool_name || ev.tool || 'tool')
      const tArgs =
        ev.input || ev.parameters || ev.arguments || ev.args || undefined
      const { target, detail } = extractToolTargetAndDetail(tArgs)
      entries.push({
        id: nextId(),
        kind: 'tool',
        toolName: tName,
        toolCategory: classifyToolCategory(tName),
        toolStatus: 'running',
        title: tName,
        subtitle: target || undefined,
        detail,
      })
      return
    }

    if (
      evType === 'tool_result' ||
      evType === 'function_response' ||
      evType === 'functionresponse'
    ) {
      const status =
        ev.is_error || String(ev.status || '').toLowerCase() === 'error'
          ? 'error'
          : 'ok'
      const tId = String(ev.tool_name || ev.name || ev.tool_use_id || 'tool')
      const outText = extractResultText(ev.output || ev.content || ev.response)
      attachOrAddToolResult(tId, status, outText)
      return
    }

    if (evType === 'user' || role === 'user') {
      const content = ev.content || (msgDict ? msgDict.content : null)
      if (Array.isArray(content)) {
        for (const block of content) {
          if (block && typeof block === 'object') {
            const b = block as Record<string, unknown>
            const btype = String(b.type || '').toLowerCase()
            if (btype === 'tool_result' || btype === 'function_response') {
              const status =
                b.is_error || String(b.status || '').toLowerCase() === 'error'
                  ? 'error'
                  : 'ok'
              const tId = String(
                b.tool_name || b.name || b.tool_use_id || 'tool'
              )
              const outText = extractResultText(b.content || b.output)
              attachOrAddToolResult(tId, status, outText)
            }
          }
        }
      }
      return
    }

    if (
      evType === 'message' ||
      evType === 'assistant' ||
      evType === 'content' ||
      evType === 'text' ||
      evType === 'delta' ||
      role === 'assistant'
    ) {
      const content =
        ev.content ?? ev.text ?? ev.delta ?? (msgDict ? msgDict.content : null)
      if (typeof content === 'string' && content.trim()) {
        pushTextMessage(content)
        return
      }
      if (Array.isArray(content)) {
        for (const block of content) {
          if (!block || typeof block !== 'object') continue
          const b = block as Record<string, unknown>
          const btype = String(b.type || '').toLowerCase()
          if ((btype === 'thinking' || btype === 'thought') && b.thinking) {
            entries.push({
              id: nextId(),
              kind: 'thinking',
              title: String(b.thinking).trim(),
            })
          } else if (
            btype === 'tool_use' ||
            btype === 'tool_call' ||
            btype === 'function_call'
          ) {
            const tName = String(b.name || b.tool_name || 'tool')
            const tArgs = b.input || b.arguments || b.args
            const { target, detail } = extractToolTargetAndDetail(tArgs)
            entries.push({
              id: nextId(),
              kind: 'tool',
              toolName: tName,
              toolCategory: classifyToolCategory(tName),
              toolStatus: 'running',
              title: tName,
              subtitle: target || undefined,
              detail,
            })
          } else if (btype === 'tool_result' || btype === 'function_response') {
            const status = b.is_error ? 'error' : 'ok'
            const tName = String(
              b.tool_name || b.name || b.tool_use_id || 'tool'
            )
            const outText = extractResultText(b.content || b.output)
            attachOrAddToolResult(tName, status, outText)
          } else if (typeof b.text === 'string' && b.text.trim()) {
            pushTextMessage(b.text)
          }
        }
      }
      return
    }

    if (evType === 'result') {
      const resText = String(ev.result || ev.response || ev.output || '').trim()
      if (resText) {
        pushTextMessage(resText)
      } else {
        entries.push({
          id: nextId(),
          kind: 'system',
          title: 'Agent turn completed',
        })
      }
      return
    }

    if (evType === 'error') {
      const errText = String(ev.error || ev.message || JSON.stringify(ev)).trim()
      entries.push({
        id: nextId(),
        kind: 'error',
        title: errText,
      })
    }
  }

  const lines = rawLog.split(/\r?\n/)
  for (let idx = 0; idx < lines.length; idx++) {
    const rawLine = lines[idx]
    const trimmed = rawLine.trim()
    if (!trimmed) continue

    // 1. Raw JSON line
    if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
      try {
        const parsed = JSON.parse(trimmed) as Record<string, unknown>
        if (parsed && typeof parsed === 'object') {
          processJsonEvent(parsed)
          continue
        }
      } catch {
        // Fall through if not valid single-line JSON
      }
    }

    // 2. Normalized `[thinking] ...`
    if (trimmed.startsWith('[thinking]')) {
      const thought = trimmed.slice('[thinking]'.length).trim()
      if (thought) {
        entries.push({
          id: nextId(),
          kind: 'thinking',
          title: thought,
        })
      }
      continue
    }

    // 3. Normalized `[tool] <name> <args>`
    if (trimmed.startsWith('[tool]')) {
      const rest = trimmed.slice('[tool]'.length).trim()
      const spaceIdx = rest.indexOf(' ')
      const toolName = spaceIdx === -1 ? rest : rest.slice(0, spaceIdx)
      const rawArgsStr = spaceIdx === -1 ? '' : rest.slice(spaceIdx + 1).trim()
      const { target, detail } = extractToolTargetAndDetail(rawArgsStr)
      entries.push({
        id: nextId(),
        kind: 'tool',
        toolName: toolName || 'tool',
        toolCategory: classifyToolCategory(toolName || 'tool'),
        toolStatus: 'running',
        title: toolName || 'tool',
        subtitle: target || undefined,
        detail,
      })
      continue
    }

    // 4. Normalized `[tool:ok]` or `[tool:error]`
    const toolResultMatch = trimmed.match(
      /^\[tool:(ok|error)\]\s*([^\s]+)(?:\s*->\s*(.*))?$/
    )
    if (toolResultMatch) {
      const status = toolResultMatch[1] as 'ok' | 'error'
      const toolId = toolResultMatch[2]
      const preview = toolResultMatch[3] || ''
      attachOrAddToolResult(toolId, status, preview)
      continue
    }

    // 5. Normalized `[agent]` or `[agent:error]`
    if (trimmed.startsWith('[agent:error]')) {
      entries.push({
        id: nextId(),
        kind: 'error',
        title: trimmed.slice('[agent:error]'.length).trim(),
      })
      continue
    }
    if (trimmed.startsWith('[agent]')) {
      entries.push({
        id: nextId(),
        kind: 'system',
        title: trimmed.slice('[agent]'.length).trim(),
      })
      continue
    }

    // 6. Python Supervisor logs: `2026-11-11 17:00:00,123 [INFO] ...`
    const supMatch = trimmed.match(
      /^\d{4}-\d{2}-\d{2}\s+(\d{2}:\d{2}:\d{2})(?:,\d+)?\s+\[(INFO|WARNING|ERROR)\]\s+(.*)$/
    )
    if (supMatch) {
      const [, timeStr, level, msg] = supMatch
      if (level === 'ERROR') {
        entries.push({
          id: nextId(),
          kind: 'error',
          timestamp: timeStr,
          title: msg.trim(),
        })
      } else {
        entries.push({
          id: nextId(),
          kind: 'system',
          timestamp: timeStr,
          title: msg.trim(),
        })
      }
      continue
    }

    // 7. Regular agent output text
    pushTextMessage(trimmed)
  }

  // Finalize any tool calls that didn't emit an explicit tool_result line:
  // If another entry came after it, or if the run has finished, mark it 'ok'.
  for (let i = 0; i < entries.length; i++) {
    const entry = entries[i]
    if (entry.kind === 'tool' && entry.toolStatus === 'running') {
      const isLastEntry = i === entries.length - 1
      if (!isLastEntry || !isRunActive) {
        entry.toolStatus = 'ok'
      }
    }
  }

  return entries
}

