import React from 'react'
import {
  GitMerge,
  GitPullRequest,
  Lightbulb,
  Sparkles,
} from 'lucide-react'

interface StreamPair {
  id: string
  laneClass: string
  delay: string
  inbound: {
    label: string
    icon: React.ElementType
    colorClass: string
    dotClass: string
  }
  outbound: {
    label: string
    icon: React.ElementType
    colorClass: string
    dotClass: string
  }
}

const STREAM_DURATION = '26s'

const STREAM_PAIRS: StreamPair[] = [
  {
    id: 'lane1-node1',
    laneClass: 'top-6',
    delay: '0s',
    inbound: {
      label: 'seed · oauth-pkce-flow',
      icon: Sparkles,
      colorClass:
        'border-lime-500/40 text-lime-700 dark:text-lime-300 shadow-lime-500/10 bg-gradient-to-r from-lime-500/12 to-lime-500/[0.03]',
      dotClass: 'bg-lime-400',
    },
    outbound: {
      label: 'pr merged · oauth-pkce-flow',
      icon: GitPullRequest,
      colorClass:
        'border-emerald-500/40 text-emerald-700 dark:text-emerald-300 shadow-emerald-500/10 bg-gradient-to-r from-emerald-500/12 to-emerald-500/[0.03]',
      dotClass: 'bg-emerald-400',
    },
  },
  {
    id: 'lane1-node2',
    laneClass: 'top-6',
    delay: '-13s',
    inbound: {
      label: 'seed · pg-read-replicas',
      icon: Lightbulb,
      colorClass:
        'border-sky-500/40 text-sky-700 dark:text-sky-300 shadow-sky-500/10 bg-gradient-to-r from-sky-500/12 to-sky-500/[0.03]',
      dotClass: 'bg-sky-400',
    },
    outbound: {
      label: 'pr merged · pg-read-replicas',
      icon: GitMerge,
      colorClass:
        'border-teal-500/40 text-teal-700 dark:text-teal-300 shadow-teal-500/10 bg-gradient-to-r from-teal-500/12 to-teal-500/[0.03]',
      dotClass: 'bg-teal-400',
    },
  },
  {
    id: 'lane2-node1',
    laneClass: 'top-24',
    delay: '-4.3s',
    inbound: {
      label: 'seed · webhook-idempotency',
      icon: Sparkles,
      colorClass:
        'border-indigo-500/40 text-indigo-700 dark:text-indigo-300 shadow-indigo-500/10 bg-gradient-to-r from-indigo-500/12 to-indigo-500/[0.03]',
      dotClass: 'bg-indigo-400',
    },
    outbound: {
      label: 'pr merged · webhook-idempotency',
      icon: GitPullRequest,
      colorClass:
        'border-emerald-500/40 text-emerald-700 dark:text-emerald-300 shadow-emerald-500/10 bg-gradient-to-r from-emerald-500/12 to-emerald-500/[0.03]',
      dotClass: 'bg-emerald-400',
    },
  },
  {
    id: 'lane2-node2',
    laneClass: 'top-24',
    delay: '-17.3s',
    inbound: {
      label: 'seed · redis-rate-limiter',
      icon: Lightbulb,
      colorClass:
        'border-amber-500/40 text-amber-700 dark:text-amber-300 shadow-amber-500/10 bg-gradient-to-r from-amber-500/12 to-amber-500/[0.03]',
      dotClass: 'bg-amber-400',
    },
    outbound: {
      label: 'pr merged · redis-rate-limiter',
      icon: GitMerge,
      colorClass:
        'border-emerald-500/40 text-emerald-700 dark:text-emerald-300 shadow-emerald-500/10 bg-gradient-to-r from-emerald-500/12 to-emerald-500/[0.03]',
      dotClass: 'bg-emerald-400',
    },
  },
  {
    id: 'lane3-node1',
    laneClass: 'top-[168px]',
    delay: '-8.6s',
    inbound: {
      label: 'seed · rbac-role-gates',
      icon: Sparkles,
      colorClass:
        'border-lime-500/40 text-lime-700 dark:text-lime-300 shadow-lime-500/10 bg-gradient-to-r from-lime-500/12 to-lime-500/[0.03]',
      dotClass: 'bg-lime-400',
    },
    outbound: {
      label: 'pr merged · rbac-role-gates',
      icon: GitPullRequest,
      colorClass:
        'border-teal-500/40 text-teal-700 dark:text-teal-300 shadow-teal-500/10 bg-gradient-to-r from-teal-500/12 to-teal-500/[0.03]',
      dotClass: 'bg-teal-400',
    },
  },
  {
    id: 'lane3-node2',
    laneClass: 'top-[168px]',
    delay: '-21.6s',
    inbound: {
      label: 'seed · multi-repo-sync',
      icon: Lightbulb,
      colorClass:
        'border-sky-500/40 text-sky-700 dark:text-sky-300 shadow-sky-500/10 bg-gradient-to-r from-sky-500/12 to-sky-500/[0.03]',
      dotClass: 'bg-sky-400',
    },
    outbound: {
      label: 'pr merged · multi-repo-sync',
      icon: GitMerge,
      colorClass:
        'border-emerald-500/40 text-emerald-700 dark:text-emerald-300 shadow-emerald-500/10 bg-gradient-to-r from-emerald-500/12 to-emerald-500/[0.03]',
      dotClass: 'bg-emerald-400',
    },
  },
]

export const SdlcStreamAnimation: React.FC = () => {
  return (
    <div
      aria-hidden="true"
      className="motion-reduce:hidden absolute top-1/2 -translate-y-1/2 left-0 right-0 h-56 pointer-events-none select-none overflow-hidden z-0"
    >
      <div
        className="absolute top-[37px] left-0 right-0 h-px bg-gradient-to-r from-transparent via-emerald-400/35 dark:via-lime-400 to-transparent z-0"
        style={{ animation: 'rail-pulse-lime 5s ease-in-out infinite' }}
      />
      <div
        className="absolute top-[109px] left-0 right-0 h-px bg-gradient-to-r from-transparent via-sky-400/40 dark:via-teal-400 to-transparent z-0"
        style={{ animation: 'rail-pulse-teal 4s ease-in-out infinite' }}
      />
      <div
        className="absolute top-[181px] left-0 right-0 h-px bg-gradient-to-r from-transparent via-indigo-400/35 dark:via-indigo-400 to-transparent z-0"
        style={{ animation: 'rail-pulse-indigo 6s ease-in-out infinite', animationDelay: '-2s' }}
      />

      <div
        className="absolute top-[37px] left-0 z-0"
        style={{
          animation: `sdlc-traverse ${STREAM_DURATION} linear infinite`,
          animationDelay: '-6.5s',
        }}
      >
        <div className="w-2 h-2 rounded-full bg-emerald-400/80 dark:bg-lime-400/80 shadow-[0_0_6px_rgba(52,211,153,0.3)] dark:shadow-[0_0_8px_#a3e635] -translate-y-1/2" />
      </div>
      <div
        className="absolute top-[37px] left-0 z-0"
        style={{
          animation: `sdlc-traverse ${STREAM_DURATION} linear infinite`,
          animationDelay: '-19.5s',
        }}
      >
        <div className="w-2 h-2 rounded-full bg-sky-400/80 shadow-[0_0_8px_#38bdf8] -translate-y-1/2" />
      </div>

      <div
        className="absolute top-[109px] left-0 z-0"
        style={{
          animation: `sdlc-traverse ${STREAM_DURATION} linear infinite`,
          animationDelay: '-10.8s',
        }}
      >
        <div className="w-2 h-2 rounded-full bg-teal-400/80 shadow-[0_0_10px_#2dd4bf] -translate-y-1/2" />
      </div>
      <div
        className="absolute top-[109px] left-0 z-0"
        style={{
          animation: `sdlc-traverse ${STREAM_DURATION} linear infinite`,
          animationDelay: '-23.8s',
        }}
      >
        <div className="w-2 h-2 rounded-full bg-emerald-400/80 shadow-[0_0_10px_#34d399] -translate-y-1/2" />
      </div>

      <div
        className="absolute top-[181px] left-0 z-0"
        style={{
          animation: `sdlc-traverse ${STREAM_DURATION} linear infinite`,
          animationDelay: '-2.1s',
        }}
      >
        <div className="w-2 h-2 rounded-full bg-indigo-400/80 shadow-[0_0_8px_#818cf8] -translate-y-1/2" />
      </div>
      <div
        className="absolute top-[181px] left-0 z-0"
        style={{
          animation: `sdlc-traverse ${STREAM_DURATION} linear infinite`,
          animationDelay: '-15.1s',
        }}
      >
        <div className="w-2 h-2 rounded-full bg-teal-400/80 shadow-[0_0_8px_#2dd4bf] -translate-y-1/2" />
      </div>

      {STREAM_PAIRS.map((pair) => {
        const InboundIcon = pair.inbound.icon
        const OutboundIcon = pair.outbound.icon

        return (
          <div
            key={pair.id}
            className={`absolute ${pair.laneClass} left-0 z-10`}
            style={{
              animation: `sdlc-traverse ${STREAM_DURATION} linear infinite`,
              animationDelay: pair.delay,
              willChange: 'transform',
            }}
          >
            <div
              style={{
                animation: `seed-inbound ${STREAM_DURATION} linear infinite`,
                animationDelay: pair.delay,
              }}
            >
              <div
                className={`inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-mono shadow-md border bg-white dark:bg-[#08121f] whitespace-nowrap transition-colors duration-300 ${pair.inbound.colorClass}`}
              >
                <span className="relative flex h-2 w-2">
                  <span
                    className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${pair.inbound.dotClass}`}
                  />
                  <span
                    className={`relative inline-flex rounded-full h-2 w-2 ${pair.inbound.dotClass}`}
                  />
                </span>
                <InboundIcon size={12} className="opacity-80 shrink-0" />
                <span className="font-medium tracking-tight">{pair.inbound.label}</span>
              </div>
            </div>

            <div
              className="absolute top-0 left-0"
              style={{
                animation: `merged-outbound ${STREAM_DURATION} linear infinite`,
                animationDelay: pair.delay,
              }}
            >
              <div
                className={`inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-mono shadow-md border bg-white dark:bg-[#08121f] whitespace-nowrap transition-colors duration-300 ${pair.outbound.colorClass}`}
              >
                <span className="relative flex h-2 w-2">
                  <span
                    className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${pair.outbound.dotClass}`}
                  />
                  <span
                    className={`relative inline-flex rounded-full h-2 w-2 ${pair.outbound.dotClass}`}
                  />
                </span>
                <OutboundIcon size={12} className="opacity-80 shrink-0" />
                <span className="font-medium tracking-tight">{pair.outbound.label}</span>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
