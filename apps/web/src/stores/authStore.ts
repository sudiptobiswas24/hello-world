import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface User { id: string; name: string; email: string; role: string }

interface AuthStore {
  user: User | null
  token: string | null
  refreshToken: string | null
  setAuth: (user: User, token: string, refreshToken: string) => void
  logout: () => void
}

export const useAuthStore = create<AuthStore>()(
  persist(
    set => ({
      user: null,
      token: null,
      refreshToken: null,
      setAuth: (user, token, refreshToken) => set({ user, token, refreshToken }),
      logout: () => set({ user: null, token: null, refreshToken: null })
    }),
    { name: 'auth-storage' }
  )
)
