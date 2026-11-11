import React from 'react'
import { SessionsList } from '../../sessions/SessionsList'
import { SessionStartControl } from '../../sessions/SessionStartControl'
import type { Project, ProjectRepository, Seed } from '../../../types'

export interface ProjectRunTabProps {
  project: Project
  repos: ProjectRepository[]
  projectSeeds: Seed[]
  canContribute: boolean
  onOpenSettings?: (section?: string) => void
}

/** Project → Sessions tab: the same Sessions list, filtered to this project. */
export const ProjectSandboxTab: React.FC<ProjectRunTabProps> = ({ project, canContribute }) => (
  <SessionsList
    projectId={project.id}
    actions={canContribute ? <SessionStartControl projectId={project.id} tone="primary" size="sm" /> : null}
  />
)
