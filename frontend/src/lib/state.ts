// Call state built only from backend events. Nothing here is invented: a value the backend
// hasn't sent is shown as absent, never guessed.

import type { Alert, Coercion, Entity, Envelope, Hit, Level, Risk, StatusPayload, Utterance } from "./types";

export interface CallState {
  sessionId: string | null;
  utterances: Utterance[]; // newest first
  hits: Record<string, Hit[]>;
  risk: Risk | null;
  history: { t: number; score: number; level: Level }[];
  stages: number[];
  chainTactics: Record<number, string[]>;
  mode: string | null;
  alert: Alert | null;
  claims: Entity[];
  status: Record<string, StatusPayload>;
  ended: boolean;
}

export const initialState: CallState = {
  sessionId: null,
  utterances: [],
  hits: {},
  risk: null,
  history: [],
  stages: [],
  chainTactics: {},
  mode: null,
  alert: null,
  claims: [],
  status: {},
  ended: false,
};

export type Action = { type: "reset"; sessionId: string } | { type: "event"; event: Envelope };

export function reduce(state: CallState, action: Action): CallState {
  if (action.type === "reset") return { ...initialState, sessionId: action.sessionId };
  const e = action.event;
  switch (e.type) {
    case "utterance": {
      const u = e.payload as Utterance;
      return { ...state, utterances: [u, ...state.utterances.filter((x) => x.utterance_id !== u.utterance_id)] };
    }
    case "coercion": {
      const c = e.payload as Coercion;
      const chainTactics: Record<number, string[]> = {};
      for (const step of c.chain) (chainTactics[step.stage] ??= []).push(step.tactic);
      return {
        ...state,
        hits: { ...state.hits, [c.utterance_id]: c.hits },
        stages: c.stages_reached,
        chainTactics,
        mode: c.mode,
      };
    }
    case "risk": {
      const r = e.payload as Risk;
      return { ...state, risk: r, history: [...state.history, { t: r.t_s, score: r.score, level: r.level }] };
    }
    case "entities":
      return { ...state, claims: (e.payload as { claims_so_far: Entity[] }).claims_so_far };
    case "alert":
      return { ...state, alert: e.payload as Alert };
    case "status": {
      const s = e.payload as StatusPayload;
      if (s.component === "session") return { ...state, ended: s.status === "ended" };
      return { ...state, status: { ...state.status, [s.component]: s } };
    }
    default:
      return state;
  }
}

export const LEVEL_STYLE: Record<Level, { text: string; bg: string; ring: string; stroke: string }> = {
  LOW: { text: "text-emerald-300", bg: "bg-emerald-600", ring: "ring-emerald-500", stroke: "#34d399" },
  MEDIUM: { text: "text-amber-300", bg: "bg-amber-500", ring: "ring-amber-400", stroke: "#fbbf24" },
  HIGH: { text: "text-orange-300", bg: "bg-orange-600", ring: "ring-orange-500", stroke: "#fb923c" },
  CRITICAL: { text: "text-red-300", bg: "bg-red-600", ring: "ring-red-500", stroke: "#f87171" },
};

export const TACTIC_STYLE: Record<string, string> = {
  AUTHORITY: "bg-sky-500/30 text-sky-100 ring-sky-400",
  THREAT: "bg-red-500/30 text-red-100 ring-red-400",
  URGENCY: "bg-amber-500/30 text-amber-100 ring-amber-400",
  SECRECY: "bg-fuchsia-500/30 text-fuchsia-100 ring-fuchsia-400",
  ISOLATION: "bg-violet-500/30 text-violet-100 ring-violet-400",
  PAYMENT: "bg-orange-500/30 text-orange-100 ring-orange-400",
  CREDENTIAL: "bg-rose-500/30 text-rose-100 ring-rose-400",
};
