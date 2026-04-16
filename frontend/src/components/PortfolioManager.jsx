import React, { useState, useEffect, useRef } from 'react'

async function loadConfig() {
  // dev server API → fall back to static file (production)
  const apiRes = await fetch('/api/config')
  if (apiRes.ok && apiRes.headers.get('content-type')?.includes('json')) {
    return { config: await apiRes.json(), editable: true }
  }
  const canWriteViaApi = apiRes.status === 404
  const staticRes = await fetch('/data/config.json')
  if (staticRes.ok) {
    return { config: await staticRes.json(), editable: canWriteViaApi }
  }
  throw new Error('無法載入設定檔')
}

export default function PortfolioManager({ isOpen, onClose, onSave, configKey = 'portfolio_tickers', title = '管理持有標的' }) {
  const [tickers, setTickers] = useState([])
  const [newTicker, setNewTicker] = useState('')
  const [newName, setNewName] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [editable, setEditable] = useState(false)
  const inputRef = useRef(null)

  useEffect(() => {
    if (!isOpen) return
    setError('')
    setSuccess('')
    loadConfig()
      .then(({ config, editable }) => {
        setEditable(editable)
        setTickers(
          (config[configKey] || []).map((t) => ({
            ticker: t,
            name: config.name_map?.[t] || '',
          }))
        )
      })
      .catch((e) => setError(e.message))
  }, [isOpen, configKey])

  useEffect(() => {
    if (isOpen && editable) inputRef.current?.focus()
  }, [isOpen, editable])

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
      const { config } = await loadConfig()

      config[configKey] = tickers.map((t) => t.ticker)
      config.name_map = config.name_map || {}
      tickers.forEach((t) => {
        if (t.name) config.name_map[t.ticker] = t.name
      })

      const saveRes = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      })
      let saveData = {}
      try {
        saveData = await saveRes.json()
      } catch {
        saveData = {}
      }
      if (!saveRes.ok) {
        throw new Error(saveData.error || '設定儲存失敗')
      }

      await fetch('/api/generate-logos', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tickers: tickers.map((t) => ({ ticker: t.ticker, name: t.name })),
        }),
      })

      const msg = saveData.dispatched
        ? '已儲存！已觸發更新流程，資料將在幾分鐘內自動更新'
        : '已儲存到 KV！排程會在下一次執行時更新資料'
      setSuccess(msg)
      onSave?.(configKey, tickers.map((t) => t.ticker))
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

        {/* Add form — only in dev mode */}
        {editable && (
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
        )}

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
                {editable && (
                  <button className="pm-item-rm" onClick={() => removeTicker(i)}>
                    &times;
                  </button>
                )}
              </div>
            )
          })}
          {tickers.length === 0 && (
            <div className="pm-empty">尚未加入任何標的</div>
          )}
        </div>

        {/* Actions */}
        <div className="pm-actions">
          {!editable && (
            <span className="pm-msg pm-hint">編輯請在本地 dev 環境操作</span>
          )}
          <button className="pm-btn cancel" onClick={onClose}>
            {editable ? '取消' : '關閉'}
          </button>
          {editable && (
            <button
              className="pm-btn save"
              onClick={handleSave}
              disabled={saving}
            >
              {saving ? '儲存中...' : '儲存'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
