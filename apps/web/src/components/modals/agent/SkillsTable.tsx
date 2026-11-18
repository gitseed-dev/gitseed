import React from 'react'
import {
  BookOpen,
  Edit2,
  Trash2,
} from 'lucide-react'
import { ActionMenu } from '../../common/ActionMenu'
import {
  EntityTableCard,
  EntityTableHeaderRow,
  SortableTh,
} from '../../layout/EntityPageLayout'
import type { SortDirection } from '../../../hooks/useTableSort'
import type { Agent, Skill } from '../../../types'

export type SkillSortKey = 'name' | 'agents'

export interface SkillsTableProps {
  skills: Skill[]
  agents: Agent[]
  selectedSkillId: string | null
  sortKey: SkillSortKey
  sortDirection: SortDirection
  onSort: (key: SkillSortKey) => void
  onSelectSkill: (skill: Skill) => void
  onDeleteSkill: (skill: Skill) => void
}

export const SkillsTable: React.FC<SkillsTableProps> = ({
  skills,
  agents,
  selectedSkillId,
  sortKey,
  sortDirection,
  onSort,
  onSelectSkill,
  onDeleteSkill,
}) => {
  const activeAgents = agents.filter((a) => !a.archived_at)

  return (
    <EntityTableCard>
      <EntityTableHeaderRow>
        <SortableTh<SkillSortKey>
          sortKey="name"
          currentSortKey={sortKey}
          currentSortDirection={sortDirection}
          onSort={onSort}
        >
          Skill
        </SortableTh>
        <th className="py-3 px-4 font-semibold text-zinc-500 dark:text-zinc-400">
          Description
        </th>
        <SortableTh<SkillSortKey>
          sortKey="agents"
          currentSortKey={sortKey}
          currentSortDirection={sortDirection}
          onSort={onSort}
        >
          Used by
        </SortableTh>
        <th className="py-3 px-4 text-right font-semibold text-zinc-700 dark:text-zinc-300">
          Actions
        </th>
      </EntityTableHeaderRow>
      <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800/60">
        {skills.map((skill) => {
          const usingAgents = activeAgents.filter(
            (a) => a.skills.includes(skill.id) || a.skills.includes(skill.slug)
          )
          const isSelected = selectedSkillId === skill.id

          return (
            <tr
              key={skill.id}
              onClick={() => onSelectSkill(skill)}
              className={`transition-colors cursor-pointer ${
                isSelected
                  ? 'bg-teal-500/[0.06] dark:bg-teal-500/10'
                  : 'hover:bg-zinc-100/80 dark:hover:bg-zinc-800/40'
              }`}
            >
              {/* 1. Skill Name & Slug */}
              <td className="py-3.5 px-4">
                <div className="flex items-center gap-3 min-w-0">
                  <div className="w-8 h-8 rounded-xl bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 border border-zinc-200/80 dark:border-zinc-700/80 flex items-center justify-center shrink-0">
                    <BookOpen size={15} />
                  </div>
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="font-semibold text-xs text-zinc-900 dark:text-zinc-100 truncate">
                      {skill.name}
                    </span>
                    <span className="font-mono text-xs text-zinc-500 dark:text-zinc-400">
                      {skill.slug}
                    </span>
                  </div>
                </div>
              </td>

              {/* 2. Description */}
              <td className="py-3.5 px-4 max-w-md">
                {skill.description ? (
                  <span className="text-xs text-zinc-600 dark:text-zinc-300 line-clamp-1">
                    {skill.description}
                  </span>
                ) : (
                  <span className="text-xs text-zinc-400 dark:text-zinc-500">
                    -
                  </span>
                )}
              </td>

              {/* 3. Used by */}
              <td className="py-3.5 px-4 whitespace-nowrap">
                {usingAgents.length > 0 ? (
                  <div className="flex items-center gap-1.5 flex-wrap">
                    {usingAgents.map((ag) => (
                      <span
                        key={ag.id}
                        className="px-2 py-0.5 rounded-md font-mono text-xs bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 border border-zinc-200/80 dark:border-zinc-700/80"
                      >
                        @{ag.handle}
                      </span>
                    ))}
                  </div>
                ) : (
                  <span className="text-xs text-zinc-400 dark:text-zinc-500">
                    Not used
                  </span>
                )}
              </td>

              {/* 4. Actions */}
              <td
                className="py-3.5 px-4 text-right whitespace-nowrap"
                onClick={(e) => e.stopPropagation()}
              >
                <ActionMenu
                  items={[
                    {
                      label: 'Edit Skill',
                      icon: <Edit2 size={14} />,
                      onClick: () => onSelectSkill(skill),
                    },
                    {
                      label: 'Delete Skill',
                      icon: <Trash2 size={14} />,
                      variant: 'danger' as const,
                      onClick: () => onDeleteSkill(skill),
                    },
                  ]}
                />
              </td>
            </tr>
          )
        })}
      </tbody>
    </EntityTableCard>
  )
}

