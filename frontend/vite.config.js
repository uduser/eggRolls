import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const configPath = path.resolve(__dirname, '../backend/config.json')
const logosDir = path.resolve(__dirname, 'public/logos')

function generateLogoSvg(symbol, name) {
  const colors = [
    '#E40001', '#0066CC', '#00A651', '#F5A623',
    '#9B59B6', '#1ABC9C', '#E74C3C', '#3498DB',
  ]
  let hash = 0
  for (const c of symbol) hash = ((hash << 5) - hash + c.charCodeAt(0)) | 0
  const bg = colors[Math.abs(hash) % colors.length]

  const display = name && name.length <= 4 ? name : symbol
  const size = display.length <= 2 ? 22 : display.length <= 4 ? 16 : 13

  return [
    '<svg width="56" height="56" xmlns="http://www.w3.org/2000/svg">',
    `<rect width="56" height="56" rx="4" fill="${bg}"/>`,
    `<text x="28" y="30" text-anchor="middle" dominant-baseline="central" `,
    `fill="#fff" font-family="Arial,sans-serif" font-size="${size}" font-weight="700">`,
    `${display}</text></svg>`,
  ].join('')
}

function configApiPlugin() {
  return {
    name: 'config-api',
    configureServer(server) {
      function readBody(req) {
        return new Promise((resolve, reject) => {
          const chunks = []
          req.on('data', (chunk) => chunks.push(chunk))
          req.on('end', () => resolve(Buffer.concat(chunks).toString('utf-8')))
          req.on('error', reject)
        })
      }

      // ── GET / POST  /api/config ──
      server.middlewares.use('/api/config', async (req, res, next) => {
        if (req.method === 'GET') {
          try {
            const raw = fs.readFileSync(configPath, 'utf-8')
            res.setHeader('Content-Type', 'application/json; charset=utf-8')
            res.end(raw)
          } catch (e) {
            res.statusCode = 500
            res.end(JSON.stringify({ error: e.message }))
          }
          return
        }

        if (req.method === 'POST') {
          try {
            const body = await readBody(req)
            const parsed = JSON.parse(body)
            fs.writeFileSync(configPath, JSON.stringify(parsed, null, 2) + '\n', 'utf-8')
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ ok: true }))
          } catch (e) {
            res.statusCode = 400
            res.end(JSON.stringify({ error: e.message }))
          }
          return
        }

        next()
      })

      // ── POST /api/generate-logos ──
      server.middlewares.use('/api/generate-logos', async (req, res, next) => {
        if (req.method !== 'POST') return next()
        try {
          const body = await readBody(req)
          const { tickers } = JSON.parse(body) // [{ ticker, name }]
          if (!fs.existsSync(logosDir)) fs.mkdirSync(logosDir, { recursive: true })

          const generated = []
          for (const { ticker, name } of tickers) {
            const symbol = ticker.replace(/\.(TW|TWO)$/i, '')
            const logoPath = path.join(logosDir, `${symbol}.svg`)
            if (!fs.existsSync(logoPath)) {
              fs.writeFileSync(logoPath, generateLogoSvg(symbol, name), 'utf-8')
              generated.push(symbol)
            }
          }
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ ok: true, generated }))
        } catch (e) {
          res.statusCode = 400
          res.end(JSON.stringify({ error: e.message }))
        }
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), configApiPlugin()],
  build: {
    outDir: 'dist',
  },
})
