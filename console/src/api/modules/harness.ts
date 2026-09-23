/**
 * Harness type definitions.
 *
 * The harness backend has been removed. These types are kept solely to
 * satisfy frontend imports until the remaining UI references are cleaned up.
 * API methods return rejected promises.
 */

export interface HarnessProvider {
  id: string;
  name: string;
  available: boolean;
  coming_soon: boolean;
  installed: boolean;
  authenticated: boolean;
  account: {
    type?: string;
    username?: string | null;
    email?: string | null;
    planType?: string;
  } | null;
  runtime_path: string | null;
  runtime_source: string | null;
  error: string | null;
  capabilities: HarnessCapabilities;
}

export interface HarnessCommand {
  name: string;
  description: string;
  accepts_arguments: boolean;
}

export interface HarnessApprovalPreset {
  id: string;
  name: string;
  description: string;
  settings: Record<string, unknown>;
}

export interface HarnessCapabilities {
  authentication: boolean;
  model_selection: boolean;
  reasoning_effort: boolean;
  reasoning_stream: boolean;
  tool_stream: boolean;
  session_resume: boolean;
  workspace_ui: boolean;
  native_skills_ui: boolean;
  native_tools_ui: boolean;
  native_mcp_ui: boolean;
  loop_modes: boolean;
  attachments: boolean;
  context_usage: boolean;
  skills_commands: boolean;
  qwenpaw_skills_projection: boolean;
  qwenpaw_mcp_projection: boolean;
  provider_skills_discovery: boolean;
  provider_mcp_discovery: boolean;
  mcp_tool_allowlist: boolean;
  commands: HarnessCommand[];
  approval_presets: HarnessApprovalPreset[];
}

export interface HarnessModel {
  id: string;
  name: string;
  description: string;
  is_default: boolean;
  reasoning_efforts: string[];
  default_reasoning_effort: string | null;
}

export interface HarnessDiscoveredMCPServer {
  name: string;
  provider_id: string;
  transport: string;
  enabled: boolean;
  auth_status: string;
  read_only: boolean;
  scope: "provider";
}

export interface HarnessDiscoveredSkill {
  name: string;
  description: string;
  provider_id: string;
  source: string;
  enabled: boolean;
  read_only: boolean;
  scope: "provider";
}

export interface HarnessLogin {
  type: string;
  loginId: string;
  authUrl?: string;
  verificationUrl?: string;
  userCode?: string;
  command?: string;
}

// ── API stubs (backend removed) ─────────────────────────────────────────────

const unavailable = () =>
  Promise.reject(new Error("Harness backend has been removed"));

export const harnessApi = {
  list: unavailable as () => Promise<{ providers: HarnessProvider[] }>,
  listModels: unavailable as (
    providerId: string,
  ) => Promise<{ models: HarnessModel[]; message?: string }>,
  listMCP: unavailable as (
    providerId: string,
  ) => Promise<{ servers: HarnessDiscoveredMCPServer[]; message?: string }>,
  listSkills: unavailable as (
    providerId: string,
  ) => Promise<{ skills: HarnessDiscoveredSkill[]; message?: string }>,
  status: unavailable as (
    providerId: string,
    settings?: Record<string, unknown>,
  ) => Promise<HarnessProvider>,
  login: unavailable as (
    providerId: string,
    deviceCode?: boolean,
    settings?: Record<string, unknown>,
  ) => Promise<HarnessLogin>,
  logout: unavailable as (
    providerId: string,
    settings?: Record<string, unknown>,
  ) => Promise<{ ok: boolean }>,
};
