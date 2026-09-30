// API response shapes — mirror the FastAPI endpoints in api/main.py.

export interface EncounterMeta {
  n_encounters: number;
  total_duration_s: number;
  total_damage: number;
  total_heal: number;
  avg_dps: number;
  avg_hps: number;
}

export interface EncounterRow {
  combat_id: number;
  start_dt: string;
  end_dt: string;
  duration_s: number;
  total_damage: number;
  total_heal: number;
  dps: number;
  hps: number;
  zone_name: string;
  note?: string | null;
  hidden: boolean;
}

export interface SpellRow {
  spell: string;
  total: number;
  count: number;
  avg: number;
  pct: number;
  kind?: string;
}

export interface TargetRow {
  target: string;
  total: number;
  count: number;
  avg: number;
}

export interface CombatBounds {
  start_dt: string;
  end_dt: string;
  duration_s: number;
  total_damage: number;
  total_heal: number;
  dps: number;
  hps: number;
}

export interface CombatSummary {
  combat_id: number;
  target_name: string;
  note?: string | null;
  bounds: CombatBounds;
  ttk_s: number | null;
  overkill_pct: number | null;
  total_damage_raw: number;
  total_absorb: number;
  targets: TargetRow[];
  top_damage_spells: SpellRow[];
  top_heal_spells: SpellRow[];
}

export interface TimelinePoint {
  t: number;
  dps: number;
  hps: number;
  selected_dps?: number;
  selected_hps?: number;
}

export interface CombatEvent {
  timestamp: string;
  event: string;
  source: string;
  target: string;
  spell_name: string;
  amount: number;
  effective_amount: number;
  type: string;
}

export interface RunRow {
  run_id: number;
  zone_name: string;
  zone_id: number;
  start_dt: string;
  end_dt: string;
  n_encounters: number;
  total_damage: number;
  total_heal: number;
  duration_s: number;
  [key: string]: unknown;
}

export interface TargetTotal {
  target: string;
  encounters: number;
  total_time_s: number;
  total_damage: number;
  total_heal: number;
  dps: number;
  hps: number;
}

export interface CharacterEntry {
  character: string;
  n_encounters: number;
  total_duration_s: number;
  total_damage: number;
  total_heal: number;
  avg_dps: number;
  avg_hps: number;
  top_damage_spells: SpellRow[];
  top_heal_spells: SpellRow[];
}

export interface BossRow {
  name: string;
  n_encounters: number;
  zone_name: string;
  best_dps: number;
  worst_dps: number;
  avg_dps: number;
  total_damage: number;
}

export interface Health {
  sqlite_active: boolean;
  csv_exists: boolean;
  csv_mtime?: number;
  db_age_s?: number;
  row_count?: number;
  max_combat_id?: number;
  encounter_count?: number;
  [key: string]: unknown;
}

export interface Characters {
  characters: string[];
  others?: string[];
  counts: Record<string, number>;
}

export interface Abilities {
  damage: SpellRow[];
  healing: SpellRow[];
}
