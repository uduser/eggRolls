const REPO = process.env.GITHUB_REPO || 'uduser/eggRolls'
const FILE_PATH = 'backend/config.json'
const BRANCH = 'main'

export default async function handler(req, res) {
  const token = process.env.GITHUB_TOKEN
  if (!token) {
    return res.status(500).json({ error: 'GITHUB_TOKEN not configured' })
  }

  const gh = (url, opts = {}) =>
    fetch(`https://api.github.com${url}`, {
      ...opts,
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github.v3+json',
        ...opts.headers,
      },
    })

  // ── GET: 從 GitHub 讀取 config.json ──
  if (req.method === 'GET') {
    const r = await gh(`/repos/${REPO}/contents/${FILE_PATH}?ref=${BRANCH}`)
    if (!r.ok) return res.status(r.status).json({ error: 'Failed to fetch config' })
    const data = await r.json()
    const content = Buffer.from(data.content, 'base64').toString('utf-8')
    res.setHeader('Content-Type', 'application/json; charset=utf-8')
    return res.status(200).end(content)
  }

  // ── POST: 更新 config.json 並觸發 screener workflow ──
  if (req.method === 'POST') {
    // 取得目前檔案的 SHA
    const getRes = await gh(`/repos/${REPO}/contents/${FILE_PATH}?ref=${BRANCH}`)
    if (!getRes.ok) return res.status(getRes.status).json({ error: 'Failed to read current config' })
    const current = await getRes.json()

    // 寫入新內容
    const newContent = Buffer.from(
      JSON.stringify(req.body, null, 2) + '\n'
    ).toString('base64')

    const putRes = await gh(`/repos/${REPO}/contents/${FILE_PATH}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: 'config: update tickers via web UI',
        content: newContent,
        sha: current.sha,
        branch: BRANCH,
      }),
    })

    if (!putRes.ok) {
      const err = await putRes.json()
      return res.status(putRes.status).json({ error: err.message })
    }

    // 觸發 screener workflow（自動重新產生資料 + logo）
    await gh(`/repos/${REPO}/actions/workflows/update-data.yml/dispatches`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ref: BRANCH }),
    }).catch(() => {}) // 觸發失敗不阻擋儲存

    return res.status(200).json({ ok: true })
  }

  return res.status(405).json({ error: 'Method not allowed' })
}
