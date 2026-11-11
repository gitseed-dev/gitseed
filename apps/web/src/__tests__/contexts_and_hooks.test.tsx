import assert from 'node:assert/strict'
import { before, describe, it } from 'node:test'
import React from 'react'
import {
  mockProjects,
  mockSeeds,
  mockStorage,
  mockUser,
  mockUserInvited,
  renderWithProviders,
  setupTestDom,
} from './setup'
import { useTranslation } from '../context/I18nContext'
import { useTheme } from '../context/ThemeContext'
import { useControlPlaneStatus } from '../context/StatusContext'
import { useAuth } from '../context/AuthContext'
import { useData } from '../context/DataContext'
import { buildDocumentTitle, useDocumentTitle } from '../hooks/useDocumentTitle'
import { usePagination } from '../hooks/usePagination'
import { useTableSort } from '../hooks/useTableSort'
import { VIEWS } from '../constants'

setupTestDom()

describe('Context Providers & Custom Hooks', () => {
  before(() => {
    mockStorage.setItem('gitseed_auth_user', JSON.stringify(mockUser))
    mockStorage.setItem('gitseed_org_default_theme', 'dark')
  })

  it('renders context consumers and custom hooks inside providers', () => {
    const HookConsumer: React.FC = () => {
      const { t, locale, setLocale } = useTranslation()
      const { theme, themeMode, ambientBg } = useTheme()
      const { health, isChecking } = useControlPlaneStatus()
      const { user, isAuthenticated } = useAuth()
      const data = useData()

      useDocumentTitle()

      const pagination = usePagination({
        items: [1, 2, 3, 4, 5, 6, 7],
        initialPageSize: 3,
        initialPage: 2,
      })

      const sorted = useTableSort({
        items: [
          { name: 'Beta', count: 2 },
          { name: 'Alpha', count: 5 },
        ],
        initialKey: 'name',
        initialDirection: 'asc',
      })

      return (
        <div>
          <span>locale:{locale}</span>
          <span>title:{t('app.title')}</span>
          <span>fallbackId:{t('nonexistent.id')}</span>
          <span>fallbackKey:{t('nonexistent.missing.key')}</span>
          <span>
            theme:{theme}:{themeMode}:{String(ambientBg)}
          </span>
          <span>
            status:{health.status}:{String(isChecking)}
          </span>
          <span>
            user:{user?.email}:{String(isAuthenticated)}
          </span>
          <span>projects:{data.projects.length}</span>
          <span>seeds:{data.seeds.length}</span>
          <span>runs:{data.runs.length}</span>
          <span>backends:{data.backends.length}</span>
          <span>agents:{data.agents.length}</span>
          <span>skills:{data.skills.length}</span>
          <span>policies:{data.policies.length}</span>
          <span>users:{data.users.length}</span>
          <span>events:{data.events.length}</span>
          <span>pageItems:{pagination.paginatedItems.join(',')}</span>
          <span>
            pageBounds:{pagination.startIndex}-{pagination.endIndex}/{pagination.totalPages}
          </span>
          <span>sortedFirst:{sorted.sortedItems[0]?.name}</span>
          <span>hasLocaleSetter:{typeof setLocale}</span>
        </div>
      )
    }

    const html = renderWithProviders(<HookConsumer />)
    assert.ok(html.includes('locale:en'))
    assert.ok(html.includes('fallbackId:ID'))
    assert.ok(html.includes('fallbackKey:nonexistent.missing.key'))
    assert.ok(html.includes('user::false'))
    assert.ok(html.includes('projects:2'))
    assert.ok(html.includes('seeds:2'))
    assert.ok(html.includes('runs:2'))
    assert.ok(html.includes('backends:3'))
    assert.ok(html.includes('agents:2'))
    assert.ok(html.includes('skills:2'))
    assert.ok(html.includes('policies:5'))
    assert.ok(html.includes('users:2'))
    assert.ok(html.includes('pageItems:4,5,6'))
    assert.ok(html.includes('pageBounds:4-6/3'))
    assert.ok(html.includes('sortedFirst:Alpha'))
  })

  it('supports numeric, boolean, nullish, and custom comparator sorting in useTableSort', () => {
    const SortConsumer: React.FC = () => {
      const items = [
        { id: '1', score: 10, active: false, tag: null as string | null },
        { id: '2', score: 30, active: true, tag: 'zeta' },
        { id: '3', score: 20, active: true, tag: 'alpha' },
      ]

      const byScoreDesc = useTableSort({
        items,
        initialKey: 'score',
        initialDirection: 'desc',
      })
      const byActiveAsc = useTableSort({
        items,
        initialKey: 'active',
        initialDirection: 'asc',
      })
      const byTagAsc = useTableSort({
        items,
        initialKey: 'tag',
        initialDirection: 'asc',
      })
      const byCustom = useTableSort({
        items,
        initialKey: 'id',
        initialDirection: 'asc',
        customComparators: {
          id: (a, b) => Number(b.id) - Number(a.id),
        },
      })

      return (
        <div>
          <span>scores:{byScoreDesc.sortedItems.map((i) => i.score).join(',')}</span>
          <span>active:{byActiveAsc.sortedItems.map((i) => i.id).join(',')}</span>
          <span>tags:{byTagAsc.sortedItems.map((i) => i.id).join(',')}</span>
          <span>custom:{byCustom.sortedItems.map((i) => i.id).join(',')}</span>
        </div>
      )
    }

    const html = renderWithProviders(<SortConsumer />)
    assert.ok(html.includes('scores:30,20,10'))
    assert.ok(html.includes('tags:3,2,1'))
    assert.ok(html.includes('custom:3,2,1'))
  })

  it('computes document titles across routes, views, and entities via buildDocumentTitle', () => {
    const baseOpts = {
      pathname: '/',
      isAuthenticated: true,
      activeView: VIEWS.DASHBOARD,
      activeDetailId: null,
      activeDetailTab: null,
      selectedProjectObj: mockProjects[0],
      projects: mockProjects,
      allSeeds: mockSeeds,
      users: [mockUser, mockUserInvited],
    }

    assert.equal(
      buildDocumentTitle({ ...baseOpts, pathname: '/accept-invite' }),
      'Accept Invite · GitSeed'
    )
    assert.equal(
      buildDocumentTitle({ ...baseOpts, isAuthenticated: false }),
      'Sign in · GitSeed'
    )
    assert.equal(
      buildDocumentTitle(baseOpts),
      'Overview · Core Platform · GitSeed'
    )
    assert.equal(
      buildDocumentTitle({
        ...baseOpts,
        activeView: VIEWS.SEEDS,
        activeDetailId: 'core-platform#101',
      }),
      'Implement OAuth2 PKCE flow · #101 · Core Platform · GitSeed'
    )
    assert.equal(
      buildDocumentTitle({
        ...baseOpts,
        activeView: VIEWS.PROJECTS,
        activeDetailId: 'core-platform',
        activeDetailTab: 'policies',
      }),
      'Policies · Core Platform · GitSeed'
    )
    assert.equal(
      buildDocumentTitle({
        ...baseOpts,
        activeView: VIEWS.USERS,
        activeDetailId: 'u1',
      }),
      'Ada · Users · GitSeed'
    )
  })
})
