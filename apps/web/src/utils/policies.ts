import type { Agent, ProjectRole, SeedStage, StagePolicy } from '../types'
import { SEED_STAGE_ORDER } from './seeds'

export const ORDERED_STAGES = SEED_STAGE_ORDER

export interface StageMetadata {
  stage: SeedStage
  stepNumber: number
  label: string
  shortDescription: string
  detailDescription: string
  isRequired: boolean
}

export const STAGE_METADATA: Record<SeedStage, StageMetadata> = {
  ideate: {
    stage: 'ideate',
    stepNumber: 1,
    label: 'Ideate',
    shortDescription: 'Refine problem statement, scope, and acceptance criteria',
    detailDescription:
      'Clarifies requirements, edge cases, and measurable acceptance criteria before planning.',
    isRequired: false,
  },
  plan: {
    stage: 'plan',
    stepNumber: 2,
    label: 'Plan',
    shortDescription: 'Decompose specification into an executable task DAG',
    detailDescription:
      'Breaks down the seed specification into ordered implementation tasks and target files.',
    isRequired: false,
  },
  execute: {
    stage: 'execute',
    stepNumber: 3,
    label: 'Execute',
    shortDescription: 'Run tasks in isolated containers, write tests, and open PRs',
    detailDescription:
      'Executes tasks inside sandboxed runner containers, runs test suites, and pushes commits.',
    isRequired: true,
  },
  review: {
    stage: 'review',
    stepNumber: 4,
    label: 'Review',
    shortDescription: 'Audit diffs against plan, acceptance criteria, and security rules',
    detailDescription:
      'Inspects code changes, test coverage, and security constraints prior to merge.',
    isRequired: false,
  },
  merge: {
    stage: 'merge',
    stepNumber: 5,
    label: 'Merge',
    shortDescription: 'Verify final approvals and merge pull request into target branch',
    detailDescription:
      'Confirms branch status checks and required sign-offs before merging into the default branch.',
    isRequired: true,
  },
}

export function formatRoleLabel(role: ProjectRole): string {
  switch (role) {
    case 'maintainer':
      return 'Maintainer'
    case 'developer':
      return 'Developer'
    case 'viewer':
      return 'Viewer'
  }
}

export function getUncoveredStages(
  policies: StagePolicy[],
  agents: Agent[]
): StagePolicy[] {
  const activeStageAgents = new Map(
    agents
      .filter((a) => !a.archived_at && a.kind === 'stage')
      .map((a) => [a.id, a])
  )

  return ORDERED_STAGES.map((st) => policies.find((p) => p.stage === st))
    .filter((p): p is StagePolicy => Boolean(p))
    .filter((pol) => {
      if (!pol.enabled) return false
      if (!pol.agent_id) return true
      const ag = activeStageAgents.get(pol.agent_id)
      return !ag || !ag.enabled
    })
}

