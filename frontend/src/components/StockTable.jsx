import React, { useRef, useEffect, useState } from 'react'

const SIGNAL_MAP = { strong: '多重交叉', buy: '低估買入', watch: '觀察', hold: '待觀察' }
const SELL_SIGNAL_MAP = { sell: '賣出', caution: '注意' }

const ICON_COLORS = [
  { bg: 'var(--blue-dim)', color: 'var(--blue)' },
  { bg: 'var(--green-dim)', color: 'var(--green)' },
  { bg: 'var(--purple-dim)', color: 'var(--purple)' },
  { bg: 'var(--amber-dim)', color: 'var(--amber)' },
  { bg: 'var(--red-dim)', color: 'var(--red)' },
]

const COLUMNS = [
  { key: 'symbol', label: '標的', sortable: false },
  { key: 'close', label: '收盤', right: true },
  { key: 'maDiffPct', label: 'vs MA5', right: true },
  { key: 'rsi', label: 'RSI(14)', right: true },
  { key: 'forwardEps', label: '預估EPS', right: true },
  { key: 'fairValue', label: '合理價', right: true },
  { key: 'undervalPct', label: '低估%', right: true },
  { key: 'yoyPct', label: '營收YoY', right: true },
  { key: 'volRatio', label: '量能', right: true },
  { key: 'passedCount', label: '買進', right: true },
  { key: 'sellPassedCount', label: '賣出', right: true },
]

function SortArrow({ column, sortConfig }) {
  if (column.sortable === false) return null
  const isActive = sortConfig.key === column.key
  const arrow = isActive && sortConfig.dir === 'asc' ? '▲' : '▼'
  return (
    <span className={`sort-arrow ${isActive ? 'active' : ''}`}>
      {arrow}
    </span>
  )
}

function VolSpark({ spark }) {
  if (!spark || !spark.length) return null
  return (
    <span className="vol-spark">
      {spark.map((val, i) => (
        <span
          key={i}
          className={`vol-spark-bar ${i >= spark.length - 2 ? 'highlight' : ''}`}
          style={{ height: `${Math.max(val / 100 * 20, 2)}px` }}
        />
      ))}
    </span>
  )
}

function ConditionDots({ conditions }) {
  if (!conditions) return null
  const keys = ['ma_breakout', 'rsi_in_range', 'undervalued', 'yoy_pass', 'vol_surge']
  return (
    <span className="cond-dots">
      {keys.map(k => (
        <span key={k} className={`cond-dot ${conditions[k] ? 'pass' : ''}`} />
      ))}
    </span>
  )
}

function SellConditionDots({ conditions }) {
  if (!conditions) return <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>—</span>
  const keys = ['ma_below', 'rsi_overbought', 'yoy_trend_down']
  return (
    <span className="cond-dots">
      {keys.map(k => (
        <span key={k} className={`cond-dot ${conditions[k] ? 'sell-pass' : ''}`} />
      ))}
    </span>
  )
}

export default function StockTable({ stocks, sortConfig, onSort }) {
  if (!stocks.length) {
    return (
      <div className="empty-state">
        目前沒有符合條件的標的
      </div>
    )
  }

  const wrapperRef = useRef(null)
  const [canScroll, setCanScroll] = useState(false)

  useEffect(() => {
    const el = wrapperRef.current
    if (!el) return
    const check = () => {
      setCanScroll(el.scrollWidth > el.clientWidth && el.scrollLeft + el.clientWidth < el.scrollWidth - 4)
    }
    check()
    el.addEventListener('scroll', check, { passive: true })
    window.addEventListener('resize', check)
    return () => {
      el.removeEventListener('scroll', check)
      window.removeEventListener('resize', check)
    }
  }, [stocks])

  return (
    <div
      ref={wrapperRef}
      className={`table-wrapper animate-in ${canScroll ? 'can-scroll' : ''}`}
      style={{ animationDelay: '0.2s' }}
    >
      <table className="stock-table">
        <thead>
          <tr>
            {COLUMNS.map(col => (
              <th
                key={col.key}
                className={`${col.right ? 'right' : ''} ${col.sortable !== false ? 'sortable' : ''}`}
                style={col.key === 'symbol' ? { minWidth: 150 } : undefined}
                onClick={() => col.sortable !== false && onSort(col.key)}
              >
                {col.label}
                <SortArrow column={col} sortConfig={sortConfig} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {stocks.map((stock, idx) => {
            const iconStyle = ICON_COLORS[idx % ICON_COLORS.length]
            const rsiColor = stock.rsi < 40 ? 'var(--green)' : 'var(--amber)'

            return (
              <tr key={stock.symbol}>
                {/* 標的 */}
                <td>
                  <a
                    className="ticker-cell ticker-link"
                    href={`https://www.tradingview.com/chart/?symbol=${stock.exchange || 'TWSE'}:${stock.symbol}`}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <div
                      className="ticker-icon"
                      style={{ background: iconStyle.bg, color: iconStyle.color }}
                    >
                      {stock.symbol.slice(-2)}
                    </div>
                    <div>
                      <div className="ticker-sym">{stock.symbol}</div>
                      <div className="ticker-name">{stock.name}</div>
                    </div>
                  </a>
                </td>

                {/* 收盤 */}
                <td className="right" style={{ fontFamily: 'var(--font-mono)', fontWeight: 500, fontSize: 14 }}>
                  {stock.close.toLocaleString()}
                </td>

                {/* vs MA5 */}
                <td className={`right ${stock.maDiffPct > 0 ? 'up' : 'down'}`}>
                  {stock.maDiffPct > 0 ? '+' : ''}{stock.maDiffPct}%
                </td>

                {/* RSI */}
                <td className="right">
                  <span className="rsi-bar">
                    <span className="mono">{stock.rsi}</span>
                    <span className="rsi-track">
                      <span
                        className="rsi-fill"
                        style={{
                          width: `${stock.rsi}%`,
                          background: rsiColor,
                        }}
                      />
                    </span>
                  </span>
                </td>

                {/* 預估 EPS */}
                <td className="right" style={{ color: 'var(--text-secondary)' }}>
                  {stock.forwardEps ?? '—'}
                </td>

                {/* 合理價 */}
                <td className="right" style={{ color: 'var(--blue)' }}>
                  {stock.fairValue ? stock.fairValue.toLocaleString() : '—'}
                </td>

                {/* 低估 % */}
                <td className={`right ${stock.undervalPct < 0 ? 'up' : 'down'}`}>
                  {stock.undervalPct != null ? `${stock.undervalPct}%` : '—'}
                </td>

                {/* 營收 YoY */}
                <td className={`right ${stock.yoyPct >= 10 ? 'up' : ''}`}>
                  {stock.yoyPct != null ? `+${stock.yoyPct}%` : '—'}
                </td>

                {/* 量能 */}
                <td className="right">
                  <VolSpark spark={stock.volSpark} />
                  <span style={{ color: 'var(--amber)', fontSize: 12, marginLeft: 6, fontFamily: 'var(--font-mono)' }}>
                    {stock.volRatio}x
                  </span>
                </td>

                {/* 買進訊號：條件 + pill */}
                <td className="right">
                  <ConditionDots conditions={stock.conditions} />
                  <span style={{ marginLeft: 6 }}>
                    <span className={`signal-pill ${stock.signal}`}>
                      {SIGNAL_MAP[stock.signal]}
                    </span>
                  </span>
                </td>

                {/* 賣出訊號：條件 + pill */}
                <td className="right">
                  <SellConditionDots conditions={stock.sellConditions} />
                  {stock.sellSignal && (
                    <span style={{ marginLeft: 6 }}>
                      <span className={`signal-pill ${stock.sellSignal}`}>
                        {SELL_SIGNAL_MAP[stock.sellSignal]}
                      </span>
                    </span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
