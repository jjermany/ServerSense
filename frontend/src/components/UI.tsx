import type { ReactNode } from "react";

export function PageHeader({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="page-header">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
      </div>
      {children}
    </div>
  );
}
export function Card({
  children,
  className = "",
}: {
  children?: ReactNode;
  className?: string;
}) {
  return <section className={`card ${className}`}>{children}</section>;
}
export function Metric({
  label,
  value,
  detail,
  tone = "neutral",
}: {
  label: string;
  value: string;
  detail?: string;
  tone?: string;
}) {
  return (
    <Card className={`metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </Card>
  );
}
export function Status({ value }: { value: string }) {
  const tone = ["healthy", "running", "started", "passed"].includes(
    value?.toLowerCase(),
  )
    ? "good"
    : ["warning", "unhealthy"].includes(value?.toLowerCase())
      ? "warn"
      : "bad";
  return (
    <span className={`status ${tone}`}>
      <i />
      {value || "Unknown"}
    </span>
  );
}
export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
