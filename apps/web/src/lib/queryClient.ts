import { QueryClient } from '@tanstack/react-query'
import { message } from 'antd'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
    mutations: {
      onError: (err: unknown) => {
        const msg = (err as { response?: { data?: { message?: string } }; message?: string })
          ?.response?.data?.message || (err as { message?: string })?.message || 'An error occurred'
        message.error(msg)
      }
    }
  }
})
