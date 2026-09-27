import { request } from "../request";
import type {
  AgentRequest,
  AgentsRunningConfig,
  EmbeddingModelConfig,
} from "../types";

export interface EmbeddingTestResponse {
  success: boolean;
  configured_dimensions: number;
  actual_dimensions: number | null;
  latency_ms: number;
  message: string;
}

export interface MemoryBackendDescriptor {
  id: string;
  label: string;
  source: string;
  available: boolean;
  metadata?: Record<string, unknown>;
}

// Agent API
export const agentApi = {
  agentRoot: () => request<unknown>("/agent/"),

  healthCheck: () => request<unknown>("/agent/health"),

  agentApi: (body: AgentRequest) =>
    request<unknown>("/console/chat", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getProcessStatus: () => request<unknown>("/agent/admin/status"),

  shutdownSimple: () =>
    request<void>("/agent/shutdown", {
      method: "POST",
    }),

  shutdown: () =>
    request<void>("/agent/admin/shutdown", {
      method: "POST",
    }),

  getAgentRunningConfig: () =>
    request<AgentsRunningConfig>("/workspace/running-config"),

  listMemoryBackends: () =>
    request<MemoryBackendDescriptor[]>("/agents/memory/backends"),

  updateAgentRunningConfig: (config: AgentsRunningConfig) =>
    request<AgentsRunningConfig>("/workspace/running-config", {
      method: "PUT",
      body: JSON.stringify(config),
      timeout: 10 * 60 * 1000,
    }),

  testEmbedding: (config: EmbeddingModelConfig) =>
    request<EmbeddingTestResponse>("/workspace/embedding/test", {
      method: "POST",
      body: JSON.stringify(config),
      timeout: Math.max(30, (config.health_check_timeout ?? 15) * 2 + 5) * 1000,
    }),

  getAgentLanguage: () => request<{ language: string }>("/workspace/language"),

  updateAgentLanguage: (language: string) =>
    request<{ language: string; copied_files: string[] }>(
      "/workspace/language",
      {
        method: "PUT",
        body: JSON.stringify({ language }),
      },
    ),
};
