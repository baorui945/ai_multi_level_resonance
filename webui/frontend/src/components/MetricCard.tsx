import type { ReactNode } from 'react'

export default function MetricCard({ icon, value, label, color, bg, subtext }: {
  icon: ReactNode
  value: string
  label: string
  color: string
  bg: string
  subtext?: string
}) {
  return (
    <div className="metric-card" style={{
      ['--color' as any]: color,
      ['--bg' as any]: bg,
      ['--line' as any]: `${color}22`,
    }}>
      <div className="icon">{icon}</div>
      <div className="value">{value}</div>
      <div className="label">{label}</div>
      {subtext && <div className="sub">{subtext}</div>}
    </div>
  )
}
