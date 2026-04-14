import React, { useState, useEffect, useMemo } from 'react'
import MetricCards from './components/MetricCards'
import StockTable from './components/StockTable'

const SIGNAL_LABELS = { strong: '多重交叉', buy: '低估買入', watch: '觀察' }
const FILTER_OPTIONS = [
  { key: 'all', label: '全部' },
  { key: 'strong', label: '多重交叉' },
  { key: 'buy', label: '低估買入' },
  { key: 'watch', label: '觀察' },
]

export default function App() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('all')
  const [sortConfig, setSortConfig] = useState({ key: 'passedCount', dir: 'desc' })

  // 載入資料
  useEffect(() => {
    fetch('/data/stocks.json')
      .then(res => {
        if (!res.ok) throw new Error('無法載入資料')
        return res.json()
      })
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  // 篩選 + 排序
  const filteredStocks = useMemo(() => {
    if (!data?.stocks) return []
    let stocks = data.stocks
    if (filter !== 'all') {
      stocks = stocks.filter(s => s.signal === filter)
    }
    const { key, dir } = sortConfig
    return [...stocks].sort((a, b) => {
      const aVal = a[key] ?? 0
      const bVal = b[key] ?? 0
      return dir === 'asc' ? aVal - bVal : bVal - aVal
    })
  }, [data, filter, sortConfig])

  // 統計
  const stats = useMemo(() => {
    if (!data?.stocks?.length) return null
    const stocks = data.stocks
    const avgUnderval = stocks
      .filter(s => s.undervalPct != null)
      .reduce((sum, s) => sum + s.undervalPct, 0) / stocks.filter(s => s.undervalPct != null).length
    const avgVolRatio = stocks.reduce((sum, s) => sum + s.volRatio, 0) / stocks.length
    return {
      scanned: data.totalScanned,
      passed: data.totalPassed,
      hitRate: ((data.totalPassed / data.totalScanned) * 100).toFixed(1),
      avgUnderval: avgUnderval.toFixed(1),
      avgVolRatio: avgVolRatio.toFixed(1),
    }
  }, [data])

  const handleSort = (key) => {
    setSortConfig(prev => ({
      key,
      dir: prev.key === key && prev.dir === 'desc' ? 'asc' : 'desc',
    }))
  }

  const formatTime = (isoString) => {
    if (!isoString) return ''
    const d = new Date(isoString)
    return `${d.getFullYear()}/${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  }

  // ─── Loading / Error States ───
  if (loading) {
    return (
      <div className="loading">
        <div className="loading-spinner" />
        <div>載入篩選結果中...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="empty-state">
        <div style={{ fontSize: 18, marginBottom: 8 }}>⚠</div>
        <div>{error}</div>
        <div style={{ marginTop: 8, fontSize: 12 }}>
          請確認 <code>public/data/stocks.json</code> 檔案存在
        </div>
      </div>
    )
  }

  // ─── Main Render ───
  return (
    <>
      {/* Header */}
      <div className="header animate-in">
        <h1>選股雷達</h1>
        <span className="badge">{data.totalPassed} 檔符合</span>
        <span className="timestamp">{formatTime(data.generatedAt)}</span>
      </div>

      {/* 篩選條件顯示 */}
      <div className="criteria animate-in" style={{ animationDelay: '0.05s' }}>
        <div className="criteria-label">篩選條件</div>
        <div className="criteria-tags">
          <span className="criteria-tag">收盤價 {'>'} <em>MA{data.config.ma_period}</em></span>
          <span className="criteria-tag">RSI({data.config.rsi_period}) <em>{data.config.rsi_low}–{data.config.rsi_high}</em></span>
          <span className="criteria-tag">現價 {'<'} 預估EPS × {data.config.pe_multiple} → <em>低估</em></span>
          <span className="criteria-tag">營收 YoY <em>≥ {data.config.yoy_min}%</em></span>
          <span className="criteria-tag">成交量 {'>'} <em>{data.config.vol_avg_days}日均量 × {data.config.vol_ratio_min}</em></span>
        </div>
      </div>

      {/* 統計卡 */}
      {stats && (
        <MetricCards stats={stats} delay={0.1} />
      )}

      {/* 篩選按鈕 */}
      <div className="filters animate-in" style={{ animationDelay: '0.15s' }}>
        {FILTER_OPTIONS.map(opt => (
          <button
            key={opt.key}
            className={`filter-btn ${filter === opt.key ? 'active' : ''}`}
            onClick={() => setFilter(opt.key)}
          >
            {opt.label}
            {opt.key !== 'all' && (
              <span style={{ marginLeft: 4, opacity: 0.6 }}>
                {data.stocks.filter(s => opt.key === 'all' || s.signal === opt.key).length}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* 股票表格 */}
      <StockTable
        stocks={filteredStocks}
        sortConfig={sortConfig}
        onSort={handleSort}
      />

      {/* Legend */}
      <div className="legend animate-in" style={{ animationDelay: '0.25s' }}>
        <span className="legend-item">
          <span className="legend-dot" style={{ background: 'var(--green)' }} />
          突破均線
        </span>
        <span className="legend-item">
          <span className="legend-dot" style={{ background: 'var(--blue)' }} />
          低估 {'>'} 20%
        </span>
        <span className="legend-item">
          <span className="legend-dot" style={{ background: 'var(--amber)' }} />
          量能放大
        </span>
        <span className="legend-item">
          <span className="legend-dot" style={{ background: 'var(--purple)' }} />
          多條件交叉
        </span>
      </div>
    </>
  )
}
