import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Mic, MicOff, Square } from 'lucide-react'
import { Tooltip } from './Tooltip'

interface SpeechRecognitionEventLike {
  resultIndex: number
  results: {
    length: number
    [index: number]: {
      isFinal: boolean
      [altIndex: number]: {
        transcript: string
      }
    }
  }
}

interface SpeechRecognitionErrorEventLike {
  error: string
}

interface SpeechRecognitionInstance {
  continuous: boolean
  interimResults: boolean
  lang: string
  onresult: ((event: SpeechRecognitionEventLike) => void) | null
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
  abort: () => void
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionInstance

function getSpeechRecognitionCtor(): SpeechRecognitionConstructor | null {
  if (typeof window === 'undefined') return null
  const win = window as unknown as {
    SpeechRecognition?: SpeechRecognitionConstructor
    webkitSpeechRecognition?: SpeechRecognitionConstructor
  }
  return win.SpeechRecognition || win.webkitSpeechRecognition || null
}

export interface VoiceDictationButtonProps {
  onTranscript: (text: string) => void
  continuous?: boolean
  className?: string
}

export const VoiceDictationButton: React.FC<VoiceDictationButtonProps> = ({
  onTranscript,
  continuous = true,
  className = '',
}) => {
  const [isListening, setIsListening] = useState(false)
  const [errorHint, setErrorHint] = useState<string | null>(null)
  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null)
  const onTranscriptRef = useRef(onTranscript)

  useEffect(() => {
    onTranscriptRef.current = onTranscript
  }, [onTranscript])

  const isSupported = Boolean(getSpeechRecognitionCtor())

  const stopListening = useCallback(() => {
    if (recognitionRef.current) {
      try {
        recognitionRef.current.stop()
      } catch {
        // ignore stop errors
      }
      recognitionRef.current = null
    }
    setIsListening(false)
  }, [])

  useEffect(() => {
    return () => {
      if (recognitionRef.current) {
        try {
          recognitionRef.current.abort()
        } catch {
          // ignore abort errors
        }
        recognitionRef.current = null
      }
    }
  }, [])

  const toggleListening = () => {
    if (isListening) {
      stopListening()
      return
    }

    const Ctor = getSpeechRecognitionCtor()
    if (!Ctor) {
      setErrorHint('Voice input not supported in this browser')
      return
    }

    setErrorHint(null)
    try {
      const recognition = new Ctor()
      recognition.continuous = continuous
      recognition.interimResults = false
      recognition.lang = navigator.language || 'en-US'

      recognition.onresult = (event: SpeechRecognitionEventLike) => {
        let finalChunk = ''
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const res = event.results[i]
          if (res && res.isFinal && res[0]?.transcript) {
            finalChunk += res[0].transcript
          }
        }
        const cleaned = finalChunk.trim()
        if (cleaned) {
          onTranscriptRef.current(cleaned)
        }
      }

      recognition.onerror = (event: SpeechRecognitionErrorEventLike) => {
        if (event.error === 'not-allowed') {
          setErrorHint('Microphone access denied')
        } else if (event.error !== 'aborted' && event.error !== 'no-speech') {
          setErrorHint(`Voice error: ${event.error}`)
        }
        setIsListening(false)
        recognitionRef.current = null
      }

      recognition.onend = () => {
        setIsListening(false)
        recognitionRef.current = null
      }

      recognitionRef.current = recognition
      recognition.start()
      setIsListening(true)
    } catch {
      setErrorHint('Unable to start microphone')
      setIsListening(false)
    }
  }

  if (!isSupported) {
    return (
      <Tooltip content="Voice dictation is not supported in this browser">
        <button
          type="button"
          disabled
          aria-label="Dictate"
          className={`w-6 h-6 inline-flex items-center justify-center rounded-lg text-zinc-400 dark:text-zinc-600 cursor-not-allowed ${className}`}
        >
          <MicOff size={13} />
        </button>
      </Tooltip>
    )
  }

  return (
    <span className="inline-flex items-center gap-1.5">
      {errorHint && (
        <span className="text-[11px] text-rose-500 dark:text-rose-400">
          {errorHint}
        </span>
      )}
      <Tooltip content={isListening ? 'Stop dictation' : 'Dictate'}>
        <button
          type="button"
          onClick={toggleListening}
          aria-label={isListening ? 'Stop dictation' : 'Dictate'}
          className={`w-6 h-6 inline-flex items-center justify-center rounded-lg transition cursor-pointer shrink-0 ${isListening
              ? 'bg-rose-500/15 text-rose-600 dark:text-rose-400 border border-rose-500/30 animate-pulse'
              : 'text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 hover:bg-zinc-100 dark:hover:bg-zinc-800'
            } ${className}`}
        >
          {isListening ? (
            <Square size={10} className="fill-current" />
          ) : (
            <Mic size={13} />
          )}
        </button>
      </Tooltip>
    </span>
  )
}
