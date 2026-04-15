// Production 不需要即時產生 logo，screener workflow 會自動處理
export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' })
  }
  return res.status(200).json({ ok: true, generated: [] })
}
