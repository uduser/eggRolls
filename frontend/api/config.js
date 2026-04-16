const REPO = process.env.GITHUB_REPO || 'uduser/eggRolls'
const BRANCH = process.env.GITHUB_BRANCH || 'main'
const WORKFLOW_FILE = process.env.GITHUB_WORKFLOW_FILE || 'update-data.yml'
const CONFIG_KV_KEY = process.env.CONFIG_KV_KEY || 'eggrolls:config:current'

export default async function handler(req, res) {
  // ── GET: 從 Vercel KV 讀取 config ──
  if (req.method === 'GET') {
    try {
      const config = await getKvConfig(CONFIG_KV_KEY)
      if (!config) {
        return res.status(404).json({ error: 'Config not initialized in KV' })
      }
      return res.status(200).json(config)
    } catch (e) {
      return res.status(500).json({ error: e.message || 'Failed to read KV config' })
    }
  }

  // ── POST: 寫入 KV，並嘗試觸發 screener workflow ──
  if (req.method === 'POST') {
    const payload = typeof req.body === 'string' ? safeJsonParse(req.body) : req.body
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      return res.status(400).json({ error: 'Invalid JSON config payload' })
    }

    const { _dispatch_mode, ...cleanPayload } = payload
    const configToSave = {
      ...cleanPayload,
      _meta: {
        ...(cleanPayload._meta || {}),
        updatedAt: new Date().toISOString(),
        updatedBy: 'web-ui',
      },
    }

    try {
      await setKvConfig(CONFIG_KV_KEY, configToSave)
    } catch (e) {
      return res.status(500).json({ error: e.message || 'Failed to save KV config' })
    }

    const mode = _dispatch_mode || 'full'
    const dispatch = await dispatchWorkflow(mode)
    return res.status(200).json({
      ok: true,
      dispatched: dispatch.ok,
      dispatchError: dispatch.error || null,
    })
  }

  return res.status(405).json({ error: 'Method not allowed' })
}

function safeJsonParse(raw) {
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

async function dispatchWorkflow(mode = 'full') {
  const token = process.env.GITHUB_TOKEN
  if (!token || !REPO) return { ok: false, error: 'GitHub dispatch skipped: missing token or repo' }

  try {
    const res = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github.v3+json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ ref: BRANCH, inputs: { mode } }),
    })

    if (!res.ok) {
      let msg = `GitHub dispatch failed (${res.status})`
      try {
        const data = await res.json()
        if (data?.message) msg = data.message
      } catch {
        // ignore parse failure
      }
      return { ok: false, error: msg }
    }

    return { ok: true }
  } catch (e) {
    return { ok: false, error: e.message || 'GitHub dispatch failed' }
  }
}

function getKvClientConfig() {
  const url = process.env.KV_REST_API_URL?.replace(/\/$/, '')
  const token = process.env.KV_REST_API_TOKEN
  if (!url || !token) {
    throw new Error('KV_REST_API_URL or KV_REST_API_TOKEN not configured')
  }
  return { url, token }
}

async function kvPipeline(commands) {
  const { url, token } = getKvClientConfig()
  const res = await fetch(`${url}/pipeline`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(commands),
  })
  if (!res.ok) {
    let msg = `KV request failed (${res.status})`
    try {
      const data = await res.json()
      if (Array.isArray(data) && data[0]?.error) msg = data[0].error
    } catch {
      // ignore parse failure
    }
    throw new Error(msg)
  }
  return res.json()
}

async function getKvConfig(key) {
  const data = await kvPipeline([['GET', key]])
  const raw = data?.[0]?.result
  if (raw == null) return null
  if (typeof raw === 'string') return safeJsonParse(raw)
  if (typeof raw === 'object') return raw
  return null
}

async function setKvConfig(key, value) {
  await kvPipeline([['SET', key, JSON.stringify(value)]])
}
