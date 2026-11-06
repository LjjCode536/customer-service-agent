import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export const DEMO_USERS = ['U1001', 'U1002', 'U1003', 'U1004', 'U1005']
export const DEMO_TENANTS = ['TENANT-DEMO', 'TENANT-OTHER']
export const DEMO_ROLES = ['customer', 'agent', 'manager'] as const

export type DemoRole = (typeof DEMO_ROLES)[number]
export type RuntimeMode = 'mock' | 'real'

interface RuntimeState {
  user: string
  tenant: string
  role: DemoRole
  mode: RuntimeMode
  setUser: (user: string) => void
  setTenant: (tenant: string) => void
  setRole: (role: DemoRole) => void
  setMode: (mode: RuntimeMode) => void
}

const defaults = {
  user: 'U1001',
  tenant: 'TENANT-DEMO',
  role: 'customer' as DemoRole,
  mode: 'mock' as RuntimeMode,
}

/**
 * Runtime context shared by every request.
 *
 * `mode` is a UI context switch: Mock Case runs a fixture request, Real sends a
 * custom customer message. The backend still owns the real MOCK_MODE boundary.
 */
export const useRuntime = create<RuntimeState>()(
  persist(
    (set) => ({
      ...defaults,
      setUser: (user) => set({ user }),
      setTenant: (tenant) => set({ tenant }),
      setRole: (role) => set({ role }),
      setMode: (mode) => set({ mode }),
    }),
    { name: 'customer-service-runtime' },
  ),
)
