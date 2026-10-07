import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, type Environment, type EnvironmentRequest } from './client'

const POLL_MS = 3000

export const queryKeys = {
  templates: ['templates'] as const,
  environments: ['environments'] as const,
}

export function useTemplates() {
  return useQuery({ queryKey: queryKeys.templates, queryFn: api.templates, staleTime: Infinity })
}

export function useEnvironments() {
  return useQuery({
    queryKey: queryKeys.environments,
    queryFn: api.environments,
    refetchInterval: POLL_MS,
  })
}

export function useEstimate() {
  return useMutation({ mutationFn: api.estimate })
}

function useEnvironmentMutation<TVariables>(
  mutationFn: (variables: TVariables) => Promise<Environment>,
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn,
    onSettled: () => queryClient.invalidateQueries({ queryKey: queryKeys.environments }),
  })
}

export function useCreateEnvironment() {
  return useEnvironmentMutation(
    ({ body, idempotencyKey }: { body: EnvironmentRequest; idempotencyKey: string }) =>
      api.create(body, idempotencyKey),
  )
}

export function useRetry() {
  return useEnvironmentMutation((id: string) => api.retry(id))
}

export function useDestroy() {
  return useEnvironmentMutation((id: string) => api.destroy(id))
}

export function useUnlock() {
  return useEnvironmentMutation(({ id, lockId }: { id: string; lockId: string }) =>
    api.unlock(id, lockId),
  )
}
