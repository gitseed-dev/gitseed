import React, { useEffect, useRef } from 'react'
import * as monaco from 'monaco-editor'
import editorWorker from 'monaco-editor/editor/common/services/editorWebWorkerMain.js?worker'
import jsonWorker from 'monaco-editor/languages/features/json/json.worker.js?worker'
import cssWorker from 'monaco-editor/languages/features/css/css.worker.js?worker'
import htmlWorker from 'monaco-editor/languages/features/html/html.worker.js?worker'
import tsWorker from 'monaco-editor/languages/features/typescript/ts.worker.js?worker'

export interface MonacoRuntimeProps {
  mode: 'edit' | 'diff'
  filePath: string
  language: string
  value: string
  originalValue?: string
  originalLineNumbers?: Record<number, string>
  modifiedLineNumbers?: Record<number, string>
  onLineSelect?: (lineNumber: number) => void
  readOnly?: boolean
  sideBySide?: boolean
  theme: 'light' | 'dark'
  onChange?: (nextValue: string) => void
  onSave?: () => void
  minHeight?: number
  maxHeight?: number
  autoHeight?: boolean
  className?: string
}

let monacoConfigured = false
let modelCounter = 0

function ensureMonacoConfigured(): void {
  if (monacoConfigured) return
  monacoConfigured = true

  const g = self as unknown as {
    MonacoEnvironment?: {
      getWorker: (_workerId: string, label: string) => Worker
    }
  }

  g.MonacoEnvironment = {
    getWorker(_workerId: string, label: string) {
      if (label === 'json') {
        return new jsonWorker()
      }
      if (label === 'css' || label === 'scss' || label === 'less') {
        return new cssWorker()
      }
      if (label === 'html' || label === 'handlebars' || label === 'razor') {
        return new htmlWorker()
      }
      if (label === 'typescript' || label === 'javascript') {
        return new tsWorker()
      }
      return new editorWorker()
    },
  }

  monaco.editor.defineTheme('gitseed-dark', {
    base: 'vs-dark',
    inherit: true,
    rules: [],
    colors: {
      'editor.background': '#09090b',
      'editorGutter.background': '#09090b',
      'editorLineNumber.foreground': '#52525b',
      'editorLineNumber.activeForeground': '#e4e4e7',
      'diffEditor.insertedTextBackground': '#10b98122',
      'diffEditor.removedTextBackground': '#f43f5e22',
      'diffEditor.insertedLineBackground': '#10b98118',
      'diffEditor.removedLineBackground': '#f43f5e18',
    },
  })

  monaco.editor.defineTheme('gitseed-light', {
    base: 'vs',
    inherit: true,
    rules: [],
    colors: {
      'editor.background': '#ffffff',
      'editorGutter.background': '#fafafa',
      'editorLineNumber.foreground': '#a1a1aa',
      'editorLineNumber.activeForeground': '#27272a',
      'diffEditor.insertedTextBackground': '#10b98124',
      'diffEditor.removedTextBackground': '#f43f5e20',
      'diffEditor.insertedLineBackground': '#10b98114',
      'diffEditor.removedLineBackground': '#f43f5e12',
    },
  })

  // Keep syntax validation and IntelliSense for TS/JS, but disable cross-file semantic
  // import checks since the browser does not have the container's node_modules mounted.
  const tsLang = (
    monaco.languages as unknown as {
      typescript?: {
        typescriptDefaults?: {
          setDiagnosticsOptions: (opts: Record<string, unknown>) => void
          setCompilerOptions: (opts: Record<string, unknown>) => void
        }
        javascriptDefaults?: {
          setDiagnosticsOptions: (opts: Record<string, unknown>) => void
          setCompilerOptions: (opts: Record<string, unknown>) => void
        }
      }
    }
  ).typescript

  tsLang?.typescriptDefaults?.setDiagnosticsOptions({
    noSemanticValidation: true,
    noSyntaxValidation: false,
  })
  tsLang?.javascriptDefaults?.setDiagnosticsOptions({
    noSemanticValidation: true,
    noSyntaxValidation: false,
  })
}

export function toMonacoLanguage(lang: string): string {
  switch (lang) {
    case 'tsx':
      return 'typescript'
    case 'jsx':
      return 'javascript'
    case 'bash':
      return 'shell'
    case 'docker':
      return 'dockerfile'
    case 'markup':
      return 'html'
    case 'toml':
      return 'ini'
    case 'plain':
      return 'plaintext'
    default:
      return lang || 'plaintext'
  }
}

export const MonacoRuntime: React.FC<MonacoRuntimeProps> = ({
  mode,
  filePath,
  language,
  value,
  originalValue = '',
  originalLineNumbers,
  modifiedLineNumbers,
  onLineSelect,
  readOnly = false,
  sideBySide = false,
  theme,
  onChange,
  onSave,
  minHeight = 160,
  maxHeight = 620,
  autoHeight = false,
  className = '',
}) => {
  ensureMonacoConfigured()

  const containerRef = useRef<HTMLDivElement | null>(null)
  const editorRef = useRef<monaco.editor.IStandaloneCodeEditor | null>(null)
  const diffEditorRef = useRef<monaco.editor.IStandaloneDiffEditor | null>(null)
  const modifiedModelRef = useRef<monaco.editor.ITextModel | null>(null)
  const originalModelRef = useRef<monaco.editor.ITextModel | null>(null)
  const suppressChangeRef = useRef(false)
  const onChangeRef = useRef(onChange)
  const onSaveRef = useRef(onSave)
  const onLineSelectRef = useRef(onLineSelect)
  const originalLineNumbersRef = useRef(originalLineNumbers)
  const modifiedLineNumbersRef = useRef(modifiedLineNumbers)
  const [computedHeight, setComputedHeight] = React.useState<number>(minHeight)

  useEffect(() => {
    onChangeRef.current = onChange
  }, [onChange])

  useEffect(() => {
    onSaveRef.current = onSave
  }, [onSave])

  useEffect(() => {
    onLineSelectRef.current = onLineSelect
  }, [onLineSelect])

  useEffect(() => {
    originalLineNumbersRef.current = originalLineNumbers
    modifiedLineNumbersRef.current = modifiedLineNumbers
    if (diffEditorRef.current) {
      const origEd = diffEditorRef.current.getOriginalEditor()
      const modEd = diffEditorRef.current.getModifiedEditor()
      origEd.updateOptions({
        lineNumbers: originalLineNumbers
          ? (n: number) => originalLineNumbers[n] ?? ''
          : 'on',
      })
      modEd.updateOptions({
        lineNumbers: modifiedLineNumbers
          ? (n: number) => modifiedLineNumbers[n] ?? ''
          : 'on',
      })
    } else if (editorRef.current) {
      editorRef.current.updateOptions({
        lineNumbers: modifiedLineNumbers
          ? (n: number) => modifiedLineNumbers[n] ?? ''
          : 'on',
      })
    }
  }, [originalLineNumbers, modifiedLineNumbers])

  const monacoTheme = theme === 'dark' ? 'gitseed-dark' : 'gitseed-light'
  const monacoLang = toMonacoLanguage(language)

  useEffect(() => {
    monaco.editor.setTheme(monacoTheme)
  }, [monacoTheme])

  // Re-create editor when switching between 'edit' and 'diff' mode
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const id = ++modelCounter
    const cleanPath = (filePath || 'untitled.txt').replace(/^\/+/, '')
    const modifiedUri = monaco.Uri.from({
      scheme: 'inmemory',
      path: `/modified-${id}/${cleanPath}`,
    })
    const modifiedModel = monaco.editor.createModel(value, monacoLang, modifiedUri)
    modifiedModelRef.current = modifiedModel

    let originalModel: monaco.editor.ITextModel | null = null
    let sub: monaco.IDisposable | null = null
    let sizeSub: monaco.IDisposable | null = null
    let modMouseSub: monaco.IDisposable | null = null
    let origMouseSub: monaco.IDisposable | null = null

    if (mode === 'diff') {
      const originalUri = monaco.Uri.from({
        scheme: 'inmemory',
        path: `/original-${id}/${cleanPath}`,
      })
      originalModel = monaco.editor.createModel(
        originalValue,
        monacoLang,
        originalUri
      )
      originalModelRef.current = originalModel

      const diffEditor = monaco.editor.createDiffEditor(el, {
        theme: monacoTheme,
        readOnly,
        originalEditable: false,
        renderSideBySide: sideBySide,
        automaticLayout: true,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        fontSize: 13,
        lineHeight: 20,
        fontFamily:
          'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
        renderOverviewRuler: !autoHeight,
        scrollbar: {
          alwaysConsumeMouseWheel: !autoHeight,
        },
        padding: { top: 8, bottom: 8 },
      })
      diffEditor.setModel({
        original: originalModel,
        modified: modifiedModel,
      })
      diffEditorRef.current = diffEditor

      const origEd = diffEditor.getOriginalEditor()
      const modEd = diffEditor.getModifiedEditor()
      if (originalLineNumbersRef.current) {
        origEd.updateOptions({
          lineNumbers: (n: number) => originalLineNumbersRef.current?.[n] ?? '',
        })
      }
      if (modifiedLineNumbersRef.current) {
        modEd.updateOptions({
          lineNumbers: (n: number) => modifiedLineNumbersRef.current?.[n] ?? '',
        })
      }

      modMouseSub = modEd.onMouseDown((e) => {
        const modelLine = e.target.position?.lineNumber
        if (!modelLine || !onLineSelectRef.current) return
        const mapped = modifiedLineNumbersRef.current
          ? Number(modifiedLineNumbersRef.current[modelLine])
          : modelLine
        if (Number.isInteger(mapped) && mapped > 0) {
          onLineSelectRef.current(mapped)
        }
      })

      origMouseSub = origEd.onMouseDown((e) => {
        const modelLine = e.target.position?.lineNumber
        if (!modelLine || !onLineSelectRef.current) return
        const mapped = originalLineNumbersRef.current
          ? Number(originalLineNumbersRef.current[modelLine])
          : modelLine
        if (Number.isInteger(mapped) && mapped > 0) {
          onLineSelectRef.current(mapped)
        }
      })

      modEd.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
        onSaveRef.current?.()
      })

      sub = modifiedModel.onDidChangeContent(() => {
        if (suppressChangeRef.current) return
        onChangeRef.current?.(modifiedModel.getValue())
      })

      if (autoHeight) {
        const updateHeight = () => {
          const lineCount = Math.max(
            originalModel?.getLineCount() ?? 1,
            modifiedModel.getLineCount(),
            1
          )
          const target = Math.min(
            maxHeight,
            Math.max(minHeight, lineCount * 20 + 28)
          )
          setComputedHeight(target)
        }
        updateHeight()
        sizeSub = modEd.onDidContentSizeChange(updateHeight)
      }
    } else {
      const editor = monaco.editor.create(el, {
        model: modifiedModel,
        theme: monacoTheme,
        readOnly,
        automaticLayout: true,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        fontSize: 13,
        lineHeight: 20,
        tabSize: 2,
        insertSpaces: true,
        wordWrap: 'off',
        lineNumbers: modifiedLineNumbersRef.current
          ? (n: number) => modifiedLineNumbersRef.current?.[n] ?? ''
          : 'on',
        fontFamily:
          'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
        padding: { top: 10, bottom: 10 },
      })
      editorRef.current = editor

      modMouseSub = editor.onMouseDown((e) => {
        const modelLine = e.target.position?.lineNumber
        if (!modelLine || !onLineSelectRef.current) return
        const mapped = modifiedLineNumbersRef.current
          ? Number(modifiedLineNumbersRef.current[modelLine])
          : modelLine
        if (Number.isInteger(mapped) && mapped > 0) {
          onLineSelectRef.current(mapped)
        }
      })

      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
        onSaveRef.current?.()
      })

      sub = modifiedModel.onDidChangeContent(() => {
        if (suppressChangeRef.current) return
        onChangeRef.current?.(modifiedModel.getValue())
      })

      if (autoHeight) {
        const updateHeight = () => {
          const contentHeight = editor.getContentHeight()
          const target = Math.min(
            maxHeight,
            Math.max(minHeight, contentHeight + 16)
          )
          setComputedHeight(target)
        }
        updateHeight()
        sizeSub = editor.onDidContentSizeChange(updateHeight)
      }
    }

    return () => {
      sub?.dispose()
      sizeSub?.dispose()
      modMouseSub?.dispose()
      origMouseSub?.dispose()
      if (diffEditorRef.current) {
        diffEditorRef.current.dispose()
        diffEditorRef.current = null
      }
      if (editorRef.current) {
        editorRef.current.dispose()
        editorRef.current = null
      }
      modifiedModel.dispose()
      modifiedModelRef.current = null
      if (originalModel) {
        originalModel.dispose()
        originalModelRef.current = null
      }
    }
    // We intentionally only recreate the editor instance when mode changes;
    // value, language, readOnly, and sideBySide are updated in dedicated effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode])

  // Sync language when filePath or language changes
  useEffect(() => {
    if (modifiedModelRef.current) {
      monaco.editor.setModelLanguage(modifiedModelRef.current, monacoLang)
    }
    if (originalModelRef.current) {
      monaco.editor.setModelLanguage(originalModelRef.current, monacoLang)
    }
  }, [monacoLang, filePath])

  // Sync readOnly and sideBySide options
  useEffect(() => {
    if (editorRef.current) {
      editorRef.current.updateOptions({ readOnly })
    }
    if (diffEditorRef.current) {
      diffEditorRef.current.updateOptions({
        readOnly,
        renderSideBySide: sideBySide,
      })
    }
  }, [readOnly, sideBySide])

  // Sync modified value when changed externally (e.g. switching files, discarding, or agent updates)
  useEffect(() => {
    const model = modifiedModelRef.current
    if (!model) return
    if (model.getValue() !== value) {
      suppressChangeRef.current = true
      try {
        model.setValue(value)
      } finally {
        suppressChangeRef.current = false
      }
    }
  }, [value])

  // Sync original value in diff mode
  useEffect(() => {
    const model = originalModelRef.current
    if (!model) return
    if (model.getValue() !== originalValue) {
      model.setValue(originalValue)
    }
  }, [originalValue])

  return (
    <div
      ref={containerRef}
      style={autoHeight ? { height: `${computedHeight}px` } : undefined}
      className={autoHeight ? `w-full ${className}` : `w-full h-full ${className}`}
    />
  )
}

export default MonacoRuntime

