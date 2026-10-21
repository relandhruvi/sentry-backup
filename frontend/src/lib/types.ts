// Event shapes from /ws/events/{id} (see CLAUDE.md "Events").

export type Level = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type Bi = { en: string; hi: string };

export interface Envelope<T = unknown> {
  v: number;
  type: "utterance" | "coercion" | "entities" | "risk" | "alert" | "status";
  session_id: string;
  seq: number;
  ts: string;
  payload: T;
}

export interface Utterance {
  utterance_id: string;
  t_start_s: number;
  t_end_s: number;
  end_reason: string;
  stt_status: "ok" | "unavailable" | "timeout" | "dropped" | "error";
  text: string | null;
  lang: string | null;
  stt_latency_ms: number | null;
  detail: string;
}

export interface Hit {
  tactic: string;
  pattern: string;
  span: string;
  confidence: number;
  score: number;
  negated: boolean;
  source: "lexicon" | "model" | "llm";
  counted: boolean;
}

export interface Coercion {
  utterance_id: string;
  hits: Hit[];
  score: number;
  stages_reached: number[];
  chain: { tactic: string; stage: number; utterance_id: string; t_s: number }[];
  mode: "model" | "lexicon_fallback";
  model_status: string;
}

export interface Reason {
  code: string;
  en: string;
  hi: string;
  evidence_text: string | null;
  utterance_id: string | null;
}

export interface Risk {
  utterance_id: string;
  t_s: number;
  score: number;
  level: Level;
  previous_level: Level;
  top_reasons: Reason[];
  signals: Record<string, string>;
}

export interface Entity {
  kind: string;
  value: string;
  raw: string;
}

export interface Alert {
  alert_id: string;
  level: Level;
  score: number;
  title: Bi;
  top_reasons: Reason[];
  claims: Entity[];
  verified_official_number: string | null;
  verification_status: string;
  action: Bi;
}

export interface StatusPayload {
  component: string;
  status: string;
  detail: string;
}

export interface HealthComponent {
  status: string;
  detail: string;
}
