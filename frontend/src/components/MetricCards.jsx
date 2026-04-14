import React from 'react'

export default function MetricCards({ stats, delay = 0 }) {
  const cards = [
    { label: '掃描標的', value: stats.scanned, sub: '上市 + 上櫃', color: null },
    { label: '通過篩選', value: stats.passed, sub: `命中率 ${stats.hitRate}%`, color: 'var(--green)' },
    { label: '平均低估幅度', value: `${stats.avgUnderval}%`, sub: 'vs. EPS×20', color: 'var(--blue)' },
    { label: '平均量增', value: `${stats.avgVolRatio}x`, sub: 'vs. 20日均量', color: 'var(--amber)' },
  ]

  return (
    <div className="metrics">
      {cards.map((card, i) => (
        <div
          key={card.label}
          className="metric-card animate-in"
          style={{ animationDelay: `${delay + i * 0.04}s` }}
        >
          <div className="label">{card.label}</div>
          <div className="value" style={card.color ? { color: card.color } : undefined}>
            {card.value}
          </div>
          <div className="sub">{card.sub}</div>
        </div>
      ))}
    </div>
  )
}
