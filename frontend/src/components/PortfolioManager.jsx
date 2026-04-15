import React, { useState, useEffect, useRef } from 'react'

export default function PortfolioManager({ isOpen, onClose, onSave, configKey = 'portfolio_tickers', title = '管理持有標的' }) {
  const [tickers, setTickers] = useState([])
  const [newTicker, setNewTicker] = useState('')
  const [newName, setNewName] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const inputRef = useRef(null)

  useEffect(() => {
    if (!isOpen) return
    setError('')
    setSuccess('')
    fetch('/api/config')
      .then((r) => r.json())
      .then((config) => {
        setTickers(
          (config[configKey] || []).map((t) => ({
            ticker: t,
            name: config.name_map?.[t] || '',
          }))
        )
      })
      .catch(() => setError('無法載入設定檔'))
  }, [isOpen, configKey])

  useEffect(() => {
    if (isOpen) inputRef.current?.focus()
  }, [isOpen])

  const addTicker = () => {
    let t = newTicker.trim().toUpperCase()
    if (!t) return
    if (!t.endsWith('.TW') && !t.endsWith('.TWO')) t += '.TW'
    if (tickers.some((x) => x.ticker === t)) {
      setError('已存在此標的')
      return
    }
    setTickers([...tickers, { ticker: t, name: newName.trim() }])
    setNewTicker('')
    setNewName('')
    setError('')
    inputRef.current?.focus()
  }

  const removeTicker = (idx) => setTickers(tickers.filter((_, i) => i !== idx))

  const handleSave = async () => {
    setSaving(true)
    setError('')
    try {
      const res = await fetch('/api/config')
      const config = await res.json()

      config[configKey] = tickers.map((t) => t.ticker)
      tickers.forEach((t) => {
        if (t.name) config.name_map[t.ticker] = t.name
      })

      await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      })

      await fetch('/api/generate-logos', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tickers: tickers.map((t) => ({ ticker: t.ticker, name: t.name })),
        }),
      })

      setSuccess('已儲存！請執行 python screener.py 更新資料')
      onSave?.()
      setTimeout(() => onClose(), 1500)
    } catch (e) {
      setError('儲存失敗：' + e.message)
    } finally {
      setSaving(false)
    }
  }

  if (!isOpen) return null

  return (
    <div className="pm-overlay" onClick={onClose}>
      <div className="pm-modal animate-in" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="pm-header">
          <h2>{title}</h2>
          <button className="pm-close" onClick={onClose}>
            &times;
          </button>
        </div>

        {/* Add form */}
        <div className="pm-add">
          <input
            ref={inputRef}
            className="pm-input pm-input-ticker"
            placeholder="股票代號（如 2330）"
            value={newTicker}
            onChange={(e) => setNewTicker(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && addTicker()}
          />
          <input
            className="pm-input pm-input-name"
            placeholder="中文名稱（選填）"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && addTicker()}
          />
          <button className="pm-btn-add" onClick={addTicker}>
            新增
          </button>
        </div>

        {error && <div className="pm-msg pm-error">{error}</div>}
        {success && <div className="pm-msg pm-success">{success}</div>}

        {/* Ticker list */}
        <div className="pm-list">
          {tickers.map((t, i) => {
            const symbol = t.ticker.replace(/\.(TW|TWO)$/i, '')
            return (
              <div key={t.ticker} className="pm-item">
                <img
                  className="pm-item-logo"
                  src={`/logos/${symbol}.svg`}
                  alt=""
                  onError={(e) => (e.target.style.display = 'none')}
                />
                <div className="pm-item-info">
                  <span className="pm-item-ticker">{t.ticker}</span>
                  {t.name && <span className="pm-item-name">{t.name}</span>}
                </div>
                <button className="pm-item-rm" onClick={() => removeTicker(i)}>
                  &times;
                </button>
              </div>
            )
          })}
          {tickers.length === 0 && (
            <div className="pm-empty">尚未加入任何標的</div>
          )}
        </div>

        {/* Actions */}
        <div className="pm-actions">
          <button className="pm-btn cancel" onClick={onClose}>
            取消
          </button>
          <button
            className="pm-btn save"
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? '儲存中...' : '儲存'}
          </button>
        </div>
      </div>
    </div>
  )
}
