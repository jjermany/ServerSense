export type Disk = {
  id: string;
  name: string;
  role: string;
  manufacturer: string | null;
  model: string;
  serial: string;
  interface: string | null;
  total_bytes: number;
  used_bytes: number;
  temperature_c: number | null;
  smart_status: string;
  smart_attributes: Record<string, number>;
};
export type Container = {
  id: string;
  name: string;
  image: string;
  status: string;
  health: string | null;
  uptime_seconds: number | null;
  last_state_change: string | null;
  cpu_percent: number | null;
  memory_bytes: number | null;
  restart_count: number;
};
export type Alert = {
  id: number;
  severity: string;
  type: string;
  title: string;
  message: string;
  created_at: string;
  active?: boolean;
  acknowledged_at?: string | null;
};
export type Pool = {
  name: string;
  filesystem: string | null;
  status: string;
  device_count: number;
  devices: string[];
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  raw_bytes: number;
};
export type Dashboard = {
  server: {
    name: string;
    array_status: string;
    uptime_seconds: number | null;
    pools: Pool[];
  };
  storage: {
    total_bytes: number;
    used_bytes: number;
    free_bytes: number;
    days_remaining: number | null;
    growth_bytes_per_day: number | null;
  };
  system: {
    cpu_percent: number | null;
    memory_percent: number | null;
    load_1m: number | null;
    network_rx_bytes_per_second: number | null;
    network_tx_bytes_per_second: number | null;
    network_sample_interval_seconds: number | null;
  };
  disks: Disk[];
  containers: Container[];
  alerts: Alert[];
  insights: {
    severity: string;
    title: string;
    message: string;
    source: string;
    model?: string;
    kind?: string;
    generated_at?: string;
  }[];
  demo_mode: boolean;
};
export type StoragePoint = {
  timestamp: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  projected: boolean;
};
