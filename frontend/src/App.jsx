import React, { useState, useEffect, useMemo } from 'react'
import MetricCards from './components/MetricCards'
import StockTable from './components/StockTable'

const FILTER_OPTIONS = [
  { key: 'all', label: '全部' },
  { key: 'strong', label: '多重交叉' },
  { key: 'buy', label: '低估買入' },
  { key: 'watch', label: '觀察' },
]

const TABS = [
  { key: 'portfolio', label: '手上標的' },
  { key: 'screener', label: '大盤分析' },
]

export default function App() {
  const [activeTab, setActiveTab] = useState('portfolio')
  const [screenerData, setScreenerData] = useState(null)
  const [portfolioData, setPortfolioData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('all')
  const [sortConfig, setSortConfig] = useState({ key: 'passedCount', dir: 'desc' })

  useEffect(() => {
    Promise.all([
      fetch('/data/stocks.json').then(r => r.ok ? r.json() : null),
      fetch('/data/portfolio.json').then(r => r.ok ? r.json() : null),
    ])
      .then(([stocks, portfolio]) => {
        setScreenerData(stocks)
        setPortfolioData(portfolio)
        if (!stocks && !portfolio) setError('無法載入資料')
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const activeData = activeTab === 'portfolio' ? portfolioData : screenerData

  // 篩選 + 排序
  const filteredStocks = useMemo(() => {
    if (!activeData?.stocks) return []
    let stocks = activeData.stocks
    if (filter !== 'all') {
      stocks = stocks.filter(s => s.signal === filter)
    }
    const { key, dir } = sortConfig
    return [...stocks].sort((a, b) => {
      const aVal = a[key] ?? 0
      const bVal = b[key] ?? 0
      return dir === 'asc' ? aVal - bVal : bVal - aVal
    })
  }, [activeData, activeTab, filter, sortConfig])

  // 大盤分析統計
  const screenerStats = useMemo(() => {
    if (!screenerData?.stocks?.length) return null
    const stocks = screenerData.stocks
    const avgUnderval = stocks
      .filter(s => s.undervalPct != null)
      .reduce((sum, s) => sum + s.undervalPct, 0) / stocks.filter(s => s.undervalPct != null).length
    const avgVolRatio = stocks.reduce((sum, s) => sum + s.volRatio, 0) / stocks.length
    return {
      scanned: screenerData.totalScanned,
      passed: screenerData.totalPassed,
      hitRate: ((screenerData.totalPassed / screenerData.totalScanned) * 100).toFixed(1),
      avgUnderval: avgUnderval.toFixed(1),
      avgVolRatio: avgVolRatio.toFixed(1),
    }
  }, [screenerData])

  // 手上標的統計
  const portfolioStats = useMemo(() => {
    if (!portfolioData?.stocks?.length) return null
    const stocks = portfolioData.stocks
    const avgRsi = (stocks.reduce((sum, s) => sum + (s.rsi || 0), 0) / stocks.length).toFixed(1)
    const avgMaDiff = (stocks.reduce((sum, s) => sum + (s.maDiffPct || 0), 0) / stocks.length).toFixed(1)
    const aboveMaCount = stocks.filter(s => s.maDiffPct > 0).length
    const condPass = stocks.filter(s => s.passedCount >= 3).length
    return {
      holdings: portfolioData.totalFetched,
      total: portfolioData.totalHoldings,
      avgRsi,
      avgMaDiff,
      aboveMaCount,
      condPass,
    }
  }, [portfolioData])

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
        <div>載入資料中...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="empty-state">
        <div style={{ fontSize: 18, marginBottom: 8 }}>⚠</div>
        <div>{error}</div>
        <div style={{ marginTop: 8, fontSize: 12 }}>
          請確認 <code>public/data/</code> 資料夾下有 JSON 檔案
        </div>
      </div>
    )
  }

  // ─── Main Render ───
  return (
    <>
      {/* Header */}
      <div className="header animate-in">
        <h1>Egg Rolls 雞蛋滾</h1>
        {activeData && (
          <span className="badge">
            {activeTab === 'portfolio'
              ? `${portfolioData?.totalFetched || 0} 檔持有`
              : `${screenerData?.totalPassed || 0} 檔符合`}
          </span>
        )}
        <span className="timestamp">
          {formatTime(activeData?.generatedAt)}
        </span>
      </div>

      {/* Tab Bar */}
      <div className="tab-bar animate-in" style={{ animationDelay: '0.03s' }}>
        {TABS.map(tab => (
          <button
            key={tab.key}
            className={`tab-btn ${activeTab === tab.key ? 'active' : ''}`}
            onClick={() => setActiveTab(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ─── 手上標的 ─── */}
      {activeTab === 'portfolio' && (
        <>
          {portfolioData ? (
            <>
              {/* 篩選條件顯示 */}
              <div className="criteria animate-in" style={{ animationDelay: '0.05s' }}>
                <div className="criteria-label">買進條件</div>
                <div className="criteria-tags">
                  <span className="criteria-tag">收盤價 {'>'} <em>MA{portfolioData.config.ma_period}</em></span>
                  <span className="criteria-tag">RSI({portfolioData.config.rsi_period}) <em>{portfolioData.config.rsi_low}–{portfolioData.config.rsi_high}</em></span>
                  <span className="criteria-tag">現價 {'<'} 預估EPS × {portfolioData.config.pe_multiple} → <em>低估</em></span>
                  <span className="criteria-tag">營收 YoY <em>≥ {portfolioData.config.yoy_min}%</em></span>
                  <span className="criteria-tag">成交量 {'>'} <em>{portfolioData.config.vol_avg_days}日均量 × {portfolioData.config.vol_ratio_min}</em></span>
                </div>
              </div>

              {/* 篩選按鈕 */}
              <div className="filters animate-in" style={{ animationDelay: '0.08s' }}>
                {FILTER_OPTIONS.map(opt => (
                  <button
                    key={opt.key}
                    className={`filter-btn ${filter === opt.key ? 'active' : ''}`}
                    onClick={() => setFilter(opt.key)}
                  >
                    {opt.label}
                    {opt.key !== 'all' && (
                      <span style={{ marginLeft: 4, opacity: 0.6 }}>
                        {portfolioData.stocks.filter(s => s.signal === opt.key).length}
                      </span>
                    )}
                  </button>
                ))}
              </div>

              <StockTable
                stocks={filteredStocks}
                sortConfig={sortConfig}
                onSort={handleSort}
              />

              {/* 統計卡 */}
              {portfolioStats && (
                <div className="metrics animate-in" style={{ animationDelay: '0.2s', marginTop: 20 }}>
                  <div className="metric-card">
                    <div className="label">持有標的</div>
                    <div className="value">{portfolioStats.holdings}</div>
                    <div className="sub">共 {portfolioStats.total} 檔</div>
                  </div>
                  <div className="metric-card">
                    <div className="label">達標數</div>
                    <div className="value" style={{ color: 'var(--green)' }}>{portfolioStats.condPass}</div>
                    <div className="sub">通過 ≥ 3 條件</div>
                  </div>
                  <div className="metric-card">
                    <div className="label">平均 RSI</div>
                    <div className="value" style={{ color: 'var(--blue)' }}>{portfolioStats.avgRsi}</div>
                    <div className="sub">RSI(14)</div>
                  </div>
                  <div className="metric-card">
                    <div className="label">站上均線</div>
                    <div className="value" style={{ color: 'var(--amber)' }}>{portfolioStats.aboveMaCount}</div>
                    <div className="sub">收盤 {'>'} MA5</div>
                  </div>
                </div>
              )}

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
          ) : (
            <div className="empty-state">
              尚無手上標的資料，請先執行 <code>python screener.py</code>
            </div>
          )}
        </>
      )}

      {/* ─── 大盤分析 ─── */}
      {activeTab === 'screener' && (
        <>
          {screenerData ? (
            <>
              {/* 篩選條件顯示 */}
              <div className="criteria animate-in" style={{ animationDelay: '0.05s' }}>
                <div className="criteria-label">篩選條件</div>
                <div className="criteria-tags">
                  <span className="criteria-tag">收盤價 {'>'} <em>MA{screenerData.config.ma_period}</em></span>
                  <span className="criteria-tag">RSI({screenerData.config.rsi_period}) <em>{screenerData.config.rsi_low}–{screenerData.config.rsi_high}</em></span>
                  <span className="criteria-tag">現價 {'<'} 預估EPS × {screenerData.config.pe_multiple} → <em>低估</em></span>
                  <span className="criteria-tag">營收 YoY <em>≥ {screenerData.config.yoy_min}%</em></span>
                  <span className="criteria-tag">成交量 {'>'} <em>{screenerData.config.vol_avg_days}日均量 × {screenerData.config.vol_ratio_min}</em></span>
                </div>
              </div>

              {/* 篩選按鈕 */}
              <div className="filters animate-in" style={{ animationDelay: '0.08s' }}>
                {FILTER_OPTIONS.map(opt => (
                  <button
                    key={opt.key}
                    className={`filter-btn ${filter === opt.key ? 'active' : ''}`}
                    onClick={() => setFilter(opt.key)}
                  >
                    {opt.label}
                    {opt.key !== 'all' && (
                      <span style={{ marginLeft: 4, opacity: 0.6 }}>
                        {screenerData.stocks.filter(s => opt.key === 'all' || s.signal === opt.key).length}
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

              {/* 統計卡 */}
              {screenerStats && (
                <div style={{ marginTop: 20 }}>
                  <MetricCards stats={screenerStats} delay={0.2} />
                </div>
              )}

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
          ) : (
            <div className="empty-state">
              尚無大盤分析資料，請先執行 <code>python screener.py</code>
            </div>
          )}
        </>
      )}
    </>
  )
}
