import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import type { AuthSession, LoginRequest, LoginResponse, OperatorActionResponse } from '@/types/api'

export const AUTH_KEY = ['auth', 'session']

export function useAuth() {
  return useQuery<AuthSession>({
    queryKey: AUTH_KEY,
    queryFn: () => api.get<AuthSession>('/api/auth/session'),
    staleTime: 60_000,
    refetchInterval: 60_000,
    retry: false,
  })
}

export function useLogin() {
  const qc = useQueryClient()
  return useMutation<LoginResponse, Error, LoginRequest>({
    mutationFn: (creds) => api.post<LoginResponse>('/api/auth/login', creds),
    onSuccess: () => qc.invalidateQueries({ queryKey: AUTH_KEY }),
  })
}

export function useLogout() {
  const qc = useQueryClient()
  return useMutation<OperatorActionResponse, Error>({
    mutationFn: () => api.post<OperatorActionResponse>('/api/auth/logout'),
    onSuccess: () => qc.invalidateQueries({ queryKey: AUTH_KEY }),
  })
}
