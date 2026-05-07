import { useMemo, useState } from 'react'
import { useQueries } from '@tanstack/react-query'
import { type ColumnDef } from '@tanstack/react-table'
import { api } from '@/api/client'
import type { TraderLiveResponse, TraderLivePosition, TraderHistoryResponse, TraderHistoryTrade, HlAccount } from '@/types/api'
import DataTable from '@/components/ui/DataTable'
import Badge from '@/components/ui/Badge'
import KpiTile from '@/components/ui/KpiTile'
import EquityCurve, { type EquityPoint } from '@/components/ui/EquityCurve'
import WinRateGauge from '@/components/ui/WinRateGauge'
import PnlDistribution from '@/components/ui/PnlDistribution'
import ProgressBar from '@/components/ui/ProgressBar'

type ModelKey = 'a' | 'b' | 'c' | 'd' | 'e' | 'f' | 'g'

const POLYMARKET_BANKROLL = 500

interface ModelDef {
  key: ModelKey
  short: string
  label: string
  family: string
  endpoint: string
  historyEndpoint: string
  /** Polymarket binary needs WR > 50%, HL perp with 0.30/0.20 TP/SL needs > 40% */
  breakEven: number
  /** "venue" tag shown in the hero — Polymarket paper vs Hyperliquid testnet */
  venue: 'polymarket' | 'hyperliquid'
}

const MODELS: ModelDef[] = [
  { key: 'a', short: 'A', label: 'Model A', family: 'v4 · CatBoost (heuristic fallback)', endpoint: '/api/trader/live/a', historyEndpoint: '/api/trader/history/a', breakEven: 0.50, venue: 'polymarket' },
  { key: 'b', short: 'B', label: 'Model B', family: 'v5 · CatBoost + Coinbase lead-lag',  endpoint: '/api/trader/live/b', historyEndpoint: '/api/trader/history/b', breakEven: 0.50, venue: 'polymarket' },
  { key: 'c', short: 'C', label: 'Model C', family: 'v6 · v5 + regime gate',              endpoint: '/api/trader/live/c', historyEndpoint: '/api/trader/history/c', breakEven: 0.50, venue: 'polymarket' },
  { key: 'd', short: 'D', label: 'Model D', family: 'LLM Llama-3.3 70B · HL testnet 3x perp · 4h hold', endpoint: '/api/trader/live/d', historyEndpoint: '/api/trader/history/d', breakEven: 0.38, venue: 'hyperliquid' },
  { key: 'e', short: 'E', label: 'Model E', family: 'CatBoost v5 · HL testnet 3x perp · 4h hold',       endpoint: '/api/trader/live/e', historyEndpoint: '/api/trader/history/e', breakEven: 0.38, venue: 'hyperliquid' },
  { key: 'f', short: 'F', label: 'Model F', family: 'ML + LLM ensemble (60/40) · HL 3x · 4h hold',      endpoint: '/api/trader/live/f', historyEndpoint: '/api/trader/history/f', breakEven: 0.38, venue: 'hyperliquid' },
  { key: 'g', short: 'G', label: 'Model G', family: 'RSI mean-reversion + ML filter · HL 5x · 8h hold (backtest profittevole)', endpoint: '/api/trader/live/g', historyEndpoint: '/api/trader/history/g', breakEven: 0.46, venue: 'hyperliquid' },
]

// ── Helpers ───────────────────────────────────────────────────────────────

function pnlVariant(v: number): 'ok' | 'error' | 'default' {
  return v > 0 ? 'ok' : v < 0 ? 'error' : 'default'
}

function pnlDirection(v: number): 'up' | 'down' | 'flat' {
  return v > 0 ? 'up' : v < 0 ? 'down' : 'flat'
}

function fmtSecs(secs: number | null): string {
  if (secs === null) return '—'
  if (secs < 0) return 'expired'
  const m = Math.floor(secs / 60)
  const s = secs % 60
  return `${m}m ${s}s`
}

function fmtUsd(v: number): string {
  return (v >= 0 ? '+$' : '-$') + Math.abs(v).toFixed(2)
}

function fmtAddr(addr: string): string {
  if (!addr || addr.length < 10) return addr
  return addr.slice(0, 6) + '…' + addr.slice(-4)
}

function buildEquityCurve(trades: TraderHistoryTrade[], starting: number): EquityPoint[] {
  // Trades come newest-first by default; use only settled (resolution_status complete) and reverse
  const settled = trades
    .filter(t => t.pnl_usd !== null && t.created_at)
    .slice()
    .reverse()
  if (!settled.length) return []
  let bal = starting
  const points: EquityPoint[] = [{ t: settled[0].created_at, balance: starting }]
  for (const t of settled) {
    bal += t.pnl_usd ?? 0
    points.push({ t: t.updated_at || t.created_at, balance: bal, pnl: t.pnl_usd ?? 0 })
  }
  return points
}

function summariseTrades(trades: TraderHistoryTrade[]) {
  const settled = trades.filter(t => t.pnl_usd !== null)
  const wins   = settled.filter(t => t.won === true).length
  const losses = settled.filter(t => t.won === false).length
  const pnls   = settled.map(t => t.pnl_usd as number)
  const totalPnl = pnls.reduce((a, b) => a + b, 0)
  const totalSettled = wins + losses
  const wr = totalSettled ? wins / totalSettled : 0
  const avgWin  = wins   ? pnls.filter(p => p > 0).reduce((a, b) => a + b, 0) / wins   : 0
  const avgLoss = losses ? pnls.filter(p => p < 0).reduce((a, b) => a + b, 0) / losses : 0
  const totalStaked = settled.reduce((a, t) => a + (t.stake_usd ?? 0), 0)
  const roi = totalStaked > 0 ? totalPnl / totalStaked : 0
  return { wins, losses, totalSettled, wr, totalPnl, avgWin, avgLoss, totalStaked, roi, pnls }
}

// ── Columns ───────────────────────────────────────────────────────────────

const POSITION_COLUMNS: ColumnDef<TraderLivePosition, unknown>[] = [
  {
    accessorKey: 'title', header: 'Market',
    cell: i => <span title={String(i.getValue())}>{String(i.getValue()).slice(0, 42)}</span>,
  },
  {
    accessorKey: 'side', header: 'Side', size: 50,
    cell: i => <Badge variant={String(i.getValue()) === 'YES' ? 'ok' : 'error'}>{String(i.getValue())}</Badge>,
  },
  { accessorKey: 'shares',          header: 'Shares', size: 70, cell: i => Number(i.getValue()).toFixed(2) },
  { accessorKey: 'entry_price',     header: 'Entry',  size: 65, cell: i => Number(i.getValue()).toFixed(4) },
  { accessorKey: 'side_mark_price', header: 'Mark',   size: 65, cell: i => i.getValue() !== null ? Number(i.getValue()).toFixed(4) : '—' },
  { accessorKey: 'cost_basis_usd',  header: 'Cost $', size: 65, cell: i => '$' + Number(i.getValue()).toFixed(2) },
  {
    accessorKey: 'unrealized_pnl_usd', header: 'uPnL $', size: 75,
    cell: i => {
      const v = Number(i.getValue())
      return <span className={v > 0 ? 'text-ok' : v < 0 ? 'text-error' : ''}>{fmtUsd(v)}</span>
    },
  },
  { accessorKey: 'seconds_to_close', header: 'Closes In', size: 80, cell: i => fmtSecs(i.getValue() as number | null) },
  {
    accessorKey: 'currently_winning', header: 'On track?', size: 80,
    cell: i => {
      const v = i.getValue()
      if (v === null) return <span className="muted2">—</span>
      return v ? <Badge variant="ok">YES</Badge> : <Badge variant="error">NO</Badge>
    },
  },
]

const HISTORY_COLUMNS: ColumnDef<TraderHistoryTrade, unknown>[] = [
  {
    accessorKey: 'created_at', header: 'When', size: 130,
    cell: i => new Date(String(i.getValue())).toLocaleString('en-GB', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }),
  },
  { accessorKey: 'market_id', header: 'Market', size: 80 },
  {
    accessorKey: 'side', header: 'Side', size: 50,
    cell: i => <Badge variant={String(i.getValue()) === 'YES' ? 'ok' : 'error'}>{String(i.getValue())}</Badge>,
  },
  { accessorKey: 'stake_usd',  header: 'Stake', size: 65, cell: i => '$' + Number(i.getValue()).toFixed(2) },
  { accessorKey: 'fill_price', header: 'Entry', size: 60, cell: i => Number(i.getValue()).toFixed(4) },
  {
    accessorKey: 'won', header: 'Result', size: 70,
    cell: i => {
      const v = i.getValue()
      if (v === null) return <span className="muted2">—</span>
      return v ? <Badge variant="ok">WIN</Badge> : <Badge variant="error">LOSS</Badge>
    },
  },
  {
    accessorKey: 'pnl_usd', header: 'PnL', size: 80,
    cell: i => {
      const v = i.getValue()
      if (v === null) return <span className="muted2">—</span>
      const n = Number(v)
      return <span className={n > 0 ? 'text-ok' : n < 0 ? 'text-error' : ''}>{fmtUsd(n)}</span>
    },
  },
]

// ── Cross-model leaderboard ────────────────────────────────────────────────

interface ModelStats {
  key: ModelKey
  label: string
  family: string
  realizedPnl: number
  unrealizedPnl: number
  open: number
  settled: number
  wr: number
  loaded: boolean
}

function ModelLeaderboard({
  stats, active, onPick,
}: {
  stats: ModelStats[]
  active: ModelKey
  onPick: (k: ModelKey) => void
}) {
  // Best→worst by realized PnL
  const ranked = stats.slice().sort((a, b) => b.realizedPnl - a.realizedPnl)
  return (
    <div className="leaderboard">
      {ranked.map((s, i) => {
        const total = s.realizedPnl + s.unrealizedPnl
        const isActive = s.key === active
        return (
          <button
            key={s.key}
            className={'leaderboard__row' + (isActive ? ' leaderboard__row--active' : '')}
            onClick={() => onPick(s.key)}
          >
            <span className="leaderboard__rank">#{i + 1}</span>
            <div style={{ overflow: 'hidden' }}>
              <div className="leaderboard__name">
                {s.label} <span className="muted" style={{ fontWeight: 400 }}>· {s.family}</span>
              </div>
              <div className="leaderboard__name-sub">
                {s.settled} settled · {(s.wr * 100).toFixed(1)}% WR · {s.open} open
              </div>
            </div>
            <div>
              <div className="leaderboard__metric-label">Realized</div>
              <div className={'leaderboard__metric ' + (s.realizedPnl > 0 ? 'text-ok' : s.realizedPnl < 0 ? 'text-error' : '')}>
                {fmtUsd(s.realizedPnl)}
              </div>
            </div>
            <div>
              <div className="leaderboard__metric-label">uPnL</div>
              <div className={'leaderboard__metric ' + (s.unrealizedPnl > 0 ? 'text-ok' : s.unrealizedPnl < 0 ? 'text-error' : '')}>
                {fmtUsd(s.unrealizedPnl)}
              </div>
            </div>
            <div>
              <div className="leaderboard__metric-label">Total</div>
              <div className={'leaderboard__metric ' + (total > 0 ? 'text-ok' : total < 0 ? 'text-error' : '')}>
                {fmtUsd(total)}
              </div>
            </div>
          </button>
        )
      })}
    </div>
  )
}

// ── Per-model panel (rich layout) ──────────────────────────────────────────

function ModelPanel({ model }: { model: ModelDef }) {
  const queries = useQueries({
    queries: [
      {
        queryKey: ['trader-live', model.endpoint],
        queryFn: () => api.get<TraderLiveResponse>(model.endpoint),
        refetchInterval: 4_000, staleTime: 3_000,
      },
      {
        queryKey: ['trader-history', model.historyEndpoint],
        queryFn: () => api.get<TraderHistoryResponse>(model.historyEndpoint),
        refetchInterval: 30_000, staleTime: 20_000,
      },
    ],
  })
  const liveQ = queries[0] as { data?: TraderLiveResponse; isLoading: boolean; error: unknown }
  const histQ = queries[1] as { data?: TraderHistoryResponse; isLoading: boolean }

  const summary = useMemo(
    () => summariseTrades(histQ.data?.trades ?? []),
    [histQ.data?.trades],
  )

  const live = liveQ.data
  const isHl = model.venue === 'hyperliquid'
  const hl: HlAccount | undefined = live?.hl_account
  const hlAvailable = !!(isHl && hl?.available)

  // Effective bankroll basis:
  //  - Polymarket bots: $500 paper start
  //  - HL bot E (when SDK is reachable): real wallet = account_value + spot
  //    starting balance ≈ wallet - realized_pnl (we don't have a perfect
  //    "before" snapshot, so we approximate by anchoring to the current wallet)
  const startingBalance = hlAvailable
    ? Math.max(1, (hl!.account_value_usd + hl!.spot_usdc) - (live?.realized_pnl_usd ?? 0))
    : POLYMARKET_BANKROLL

  const equity = useMemo(
    () => buildEquityCurve(histQ.data?.trades ?? [], startingBalance),
    [histQ.data?.trades, startingBalance],
  )
  const sparkline = useMemo(() => equity.map(p => ({ value: p.balance })), [equity])

  if (liveQ.error) {
    return <div className="tab-placeholder text-error">{(liveQ.error as Error).message}</div>
  }

  // Polymarket: balance = paper bankroll + paper PnL
  // HL: balance = real wallet (spot + perp account_value); the "PnL" in
  // Polymarket-paper terms is what the strategy logged, separate from the
  // actual on-chain perp PnL which is encoded in account_value already.
  const balance = hlAvailable
    ? hl!.account_value_usd + hl!.spot_usdc
    : (live ? POLYMARKET_BANKROLL + live.realized_pnl_usd : null)
  const balanceDelta = balance !== null ? balance - startingBalance : null
  const balancePct   = balanceDelta !== null && startingBalance > 0
    ? (balanceDelta / startingBalance) * 100
    : null

  return (
    <div className="tab-section">
      {/* HERO */}
      <div className="hero">
        <div className="hero__price-block">
          <span className="hero__price-label">BTC spot</span>
          <span className="hero__price hero__price--btc">
            {live?.btc_spot_usd != null
              ? '$' + live.btc_spot_usd.toLocaleString('en-US', { maximumFractionDigits: 0 })
              : '—'}
          </span>
        </div>
        <div className="hero__divider" />
        <div className="hero__meta">
          <span className="hero__meta-label">Model</span>
          <span className="hero__meta-value">{model.label} <span className="muted" style={{ fontSize: 11 }}>· {model.family}</span></span>
        </div>
        <div className="hero__meta">
          <span className="hero__meta-label">{hlAvailable ? 'HL wallet' : 'Equity'}</span>
          <span className="hero__meta-value tabular" style={{ color: balanceDelta != null && balanceDelta < 0 ? 'var(--color-error)' : balanceDelta != null && balanceDelta > 0 ? 'var(--color-ok)' : undefined }}>
            {balance !== null ? '$' + balance.toFixed(2) : '—'}
            {balancePct !== null && (
              <span className="muted" style={{ fontSize: 11, marginLeft: 8 }}>
                {balancePct >= 0 ? '+' : ''}{balancePct.toFixed(2)}%
              </span>
            )}
          </span>
          {hlAvailable && (
            <span className="muted" style={{ fontSize: 10, marginTop: 2 }}>
              wallet {fmtAddr(hl!.address)} · testnet
            </span>
          )}
        </div>
        <div className="hero__meta" style={{ marginLeft: 'auto' }}>
          <span className="hero__meta-label">Live</span>
          <div className="hero__live-pill">
            <span className="pulse-dot pulse-dot--live" />
            {live?.as_of ? new Date(live.as_of).toLocaleTimeString() : 'connecting'}
          </div>
        </div>
      </div>

      {/* Hyperliquid live wallet (only for Model E) */}
      {isHl && (
        <div className="card">
          <div className="card__title card__title--with-actions">
            <span>Hyperliquid testnet — wallet on-chain</span>
            {hl?.address && (
              <a
                className="muted"
                style={{ fontSize: 11, fontWeight: 400, textTransform: 'none', letterSpacing: 0, textDecoration: 'none' }}
                href={`https://app.hyperliquid-testnet.xyz/explorer/address/${hl.address}`}
                target="_blank" rel="noopener noreferrer"
              >
                {fmtAddr(hl.address)} ↗
              </a>
            )}
          </div>
          {!hlAvailable ? (
            <div className="muted" style={{ fontSize: 12 }}>
              SDK non raggiungibile (HL_PRIVATE_KEY non impostato sulla VPS o testnet API down).
            </div>
          ) : (
            <div className="metrics-row">
              <KpiTile
                label="Perp account"
                value={'$' + hl!.account_value_usd.toFixed(2)}
                variant="accent"
                hint="margin balance"
              />
              <KpiTile
                label="Spot USDC"
                value={'$' + hl!.spot_usdc.toFixed(2)}
                hint="unified collateral"
              />
              <KpiTile
                label="Notional position"
                value={'$' + hl!.total_ntl_pos.toFixed(2)}
                variant={hl!.total_ntl_pos > 0 ? 'accent' : 'default'}
                hint={hl!.positions.length + ' open perp'}
              />
              <KpiTile
                label="Withdrawable"
                value={'$' + hl!.withdrawable_usd.toFixed(2)}
                hint="free margin"
              />
            </div>
          )}
          {hl?.positions && hl.positions.length > 0 && (
            <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
              {hl.positions.map((p, i) => {
                const isLong = p.szi >= 0
                return (
                  <div key={i} style={{
                    display: 'flex', alignItems: 'center', gap: 12,
                    padding: '10px 12px',
                    background: 'var(--color-bg-2)',
                    border: '1px solid var(--color-border)',
                    borderRadius: 'var(--radius-sm)',
                    flexWrap: 'wrap',
                  }}>
                    <Badge variant={isLong ? 'ok' : 'error'}>{isLong ? 'LONG' : 'SHORT'}</Badge>
                    <span style={{ fontWeight: 600 }}>{p.coin}</span>
                    <span className="muted tabular" style={{ fontSize: 11 }}>
                      {Math.abs(p.szi).toFixed(5)} @ ${p.entry_px.toLocaleString('en-US', { maximumFractionDigits: 0 })}
                    </span>
                    <span className="muted" style={{ fontSize: 11 }}>{p.leverage}x cross</span>
                    <span className="muted tabular" style={{ fontSize: 11 }}>
                      ntl ${p.position_value_usd.toFixed(2)} · margin ${p.margin_used_usd.toFixed(2)}
                    </span>
                    <span
                      className={'tabular' + (p.unrealized_pnl > 0 ? ' text-ok' : p.unrealized_pnl < 0 ? ' text-error' : '')}
                      style={{ marginLeft: 'auto', fontWeight: 600, fontSize: 13 }}
                    >
                      {fmtUsd(p.unrealized_pnl)}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* KPI ROW
          For HL bots (D, E) the headline numbers are the *real* on-chain
          values from HL fills history (closed_pnl - fees) and current open
          positions, not the Polymarket-paper accounting. Paper numbers are
          surfaced as a smaller "paper" hint below.  */}
      <div className="metrics-row">
        {hlAvailable ? (
          <>
            <KpiTile
              label="HL realized PnL"
              value={fmtUsd(hl!.net_pnl_usd ?? 0)}
              variant={pnlVariant(hl!.net_pnl_usd ?? 0)}
              deltaDirection={pnlDirection(hl!.net_pnl_usd ?? 0)}
              delta={hl!.fills_count != null ? `${hl!.fills_count} fills · ${fmtUsd(hl!.realized_pnl_usd ?? 0)} − ${fmtUsd(hl!.fees_usd ?? 0)} fees` : undefined}
              hint="closedPnl − fees"
            />
            <KpiTile
              label="HL unrealized PnL"
              value={fmtUsd(hl!.positions.reduce((a, p) => a + p.unrealized_pnl, 0))}
              variant={pnlVariant(hl!.positions.reduce((a, p) => a + p.unrealized_pnl, 0))}
              hint={hl!.positions.length + ' open perp'}
            />
            <KpiTile
              label="Open notional"
              value={'$' + hl!.total_ntl_pos.toFixed(2)}
              variant={hl!.total_ntl_pos > 0 ? 'accent' : 'default'}
              hint={'margin $' + hl!.positions.reduce((a, p) => a + p.margin_used_usd, 0).toFixed(2)}
            />
            <KpiTile
              label="Paper PnL"
              value={live ? fmtUsd(live.realized_pnl_usd) : '—'}
              variant={live ? pnlVariant(live.realized_pnl_usd) : 'default'}
              hint={live && summary.totalSettled > 0 ? `${summary.totalSettled} 15m slots · ${(summary.wr * 100).toFixed(0)}% WR` : 'pre-HL accounting'}
              sparkline={sparkline.length > 1 ? sparkline : undefined}
            />
            <KpiTile
              label="Paper / HL gap"
              value={fmtUsd((live?.realized_pnl_usd ?? 0) - (hl!.net_pnl_usd ?? 0))}
              hint="why they differ ↗"
            />
          </>
        ) : (
          <>
            <KpiTile
              label="Open positions"
              value={live?.open_count ?? '—'}
              variant="accent"
              hint={live?.total_exposure_usd != null ? '$' + live.total_exposure_usd.toFixed(2) + ' exposure' : undefined}
            />
            <KpiTile
              label="Realized PnL"
              value={live ? fmtUsd(live.realized_pnl_usd) : '—'}
              variant={live ? pnlVariant(live.realized_pnl_usd) : 'default'}
              deltaDirection={live ? pnlDirection(live.realized_pnl_usd) : undefined}
              delta={live && summary.totalSettled > 0 ? `${summary.totalSettled} settled · ${(summary.wr * 100).toFixed(1)}% WR` : undefined}
              sparkline={sparkline.length > 1 ? sparkline : undefined}
            />
            <KpiTile
              label="Unrealized PnL"
              value={live ? fmtUsd(live.unrealized_pnl_usd) : '—'}
              variant={live ? pnlVariant(live.unrealized_pnl_usd) : 'default'}
            />
            <KpiTile
              label="ROI"
              value={summary.totalStaked > 0 ? (summary.roi * 100).toFixed(2) + '%' : '—'}
              variant={pnlVariant(summary.roi)}
              deltaDirection={pnlDirection(summary.roi)}
              delta={summary.totalStaked > 0 ? '$' + summary.totalStaked.toFixed(0) + ' staked' : undefined}
            />
            <KpiTile
              label="Avg win / loss"
              value={summary.totalSettled > 0
                ? '+$' + summary.avgWin.toFixed(2) + ' / -$' + Math.abs(summary.avgLoss).toFixed(2)
                : '—'
              }
              hint={summary.avgLoss !== 0 ? 'ratio ' + Math.abs(summary.avgWin / summary.avgLoss).toFixed(2) + 'x' : undefined}
            />
          </>
        )}
      </div>

      {/* HL fills table — only when HL is active for this model */}
      {hlAvailable && hl!.recent_fills && hl!.recent_fills.length > 0 && (
        <div className="card" style={{ padding: 0 }}>
          <div className="card__title card__title--with-actions" style={{ padding: '14px 18px 0 18px', marginBottom: 8 }}>
            <span>Hyperliquid fills · on-chain history</span>
            <span className="muted" style={{ fontSize: 11, fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
              {hl!.fills_count} totali · last {hl!.recent_fills.length}
            </span>
          </div>
          <div className="data-table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Direction</th>
                  <th>Size</th>
                  <th>Price</th>
                  <th>Closed PnL</th>
                  <th>Fee</th>
                  <th>Net</th>
                </tr>
              </thead>
              <tbody>
                {hl!.recent_fills.slice(0, 30).map((f) => {
                  const net = f.closed_pnl - f.fee
                  return (
                    <tr key={f.oid}>
                      <td>{new Date(f.ts_ms).toLocaleString('en-GB', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</td>
                      <td>
                        <Badge variant={f.dir.includes('Open') ? 'accent' : 'muted'}>{f.dir}</Badge>
                      </td>
                      <td>{f.size.toFixed(5)} {f.coin}</td>
                      <td>${f.price.toLocaleString('en-US', { maximumFractionDigits: 0 })}</td>
                      <td className={f.closed_pnl > 0 ? 'text-ok' : f.closed_pnl < 0 ? 'text-error' : ''}>
                        {f.closed_pnl !== 0 ? fmtUsd(f.closed_pnl) : '—'}
                      </td>
                      <td className="muted">${f.fee.toFixed(4)}</td>
                      <td className={net > 0 ? 'text-ok' : net < 0 ? 'text-error' : ''} style={{ fontWeight: 600 }}>
                        {f.closed_pnl !== 0 || f.fee !== 0 ? fmtUsd(net) : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* CHARTS ROW */}
      <div className="grid-charts">
        <div className="card">
          <div className="card__title card__title--with-actions">
            <span>Equity curve</span>
            <span className="muted" style={{ fontSize: 11, fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
              start ${startingBalance.toFixed(0)} · {summary.totalSettled} settled trades
            </span>
          </div>
          <EquityCurve data={equity} startingBalance={startingBalance} height={260} />
        </div>
        <div className="card">
          <div className="card__title">Win rate</div>
          <WinRateGauge rate={summary.wr} total={summary.totalSettled} breakEven={model.breakEven} />
          <div style={{ marginTop: 16 }}>
            <div className="muted" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '.07em', marginBottom: 6 }}>
              vs break-even ({(model.breakEven * 100).toFixed(0)}%)
            </div>
            <ProgressBar
              value={summary.wr}
              variant={summary.wr >= model.breakEven ? 'ok' : 'error'}
            />
            <div className="muted" style={{ fontSize: 10, marginTop: 4 }}>
              {summary.wins} wins / {summary.losses} losses
            </div>
          </div>
        </div>
      </div>

      {/* PNL DISTRIBUTION */}
      {summary.pnls.length > 4 && (
        <div className="card">
          <div className="card__title">Trade PnL distribution</div>
          <PnlDistribution pnls={summary.pnls} height={200} bins={20} />
        </div>
      )}

      {/* OPEN POSITIONS */}
      <div className="card" style={{ padding: 0 }}>
        <div className="card__title" style={{ padding: '14px 18px 0 18px' }}>
          Open positions
          {live?.open_count != null && (
            <span className="muted" style={{ marginLeft: 8, fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
              · {live.open_count}
            </span>
          )}
        </div>
        <DataTable data={live?.positions ?? []} columns={POSITION_COLUMNS} isLoading={liveQ.isLoading} />
      </div>

      {/* HISTORY */}
      <div className="card" style={{ padding: 0 }}>
        <div className="card__title" style={{ padding: '14px 18px 0 18px' }}>
          Storico trade
          {histQ.data?.count != null && (
            <span className="muted" style={{ marginLeft: 8, fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
              · {histQ.data.count} totali
            </span>
          )}
        </div>
        <DataTable data={histQ.data?.trades ?? []} columns={HISTORY_COLUMNS} isLoading={histQ.isLoading} />
      </div>
    </div>
  )
}

// ── Top-level page ─────────────────────────────────────────────────────────

export default function TraderDashboardPage() {
  const [active, setActive] = useState<ModelKey>('a')

  // Cross-model leaderboard pulls all models in parallel
  const leaderboardQueries = useQueries({
    queries: MODELS.flatMap(m => [
      {
        queryKey: ['trader-live', m.endpoint],
        queryFn: () => api.get<TraderLiveResponse>(m.endpoint),
        refetchInterval: 8_000, staleTime: 6_000,
      },
      {
        queryKey: ['trader-history', m.historyEndpoint],
        queryFn: () => api.get<TraderHistoryResponse>(m.historyEndpoint),
        refetchInterval: 60_000, staleTime: 45_000,
      },
    ]),
  })

  const stats: ModelStats[] = MODELS.map((m, i) => {
    const live = leaderboardQueries[i * 2]?.data as TraderLiveResponse | undefined
    const hist = leaderboardQueries[i * 2 + 1]?.data as TraderHistoryResponse | undefined
    const sum = summariseTrades(hist?.trades ?? [])
    // For HL bots, the leaderboard rank uses real on-chain PnL
    // (closedPnl − fees from HL fill history) instead of paper.
    const hl = live?.hl_account
    const useHl = m.venue === 'hyperliquid' && hl?.available
    const realizedPnl = useHl ? (hl!.net_pnl_usd ?? 0) : (live?.realized_pnl_usd ?? 0)
    const unrealizedPnl = useHl
      ? (hl!.positions.reduce((a, p) => a + p.unrealized_pnl, 0))
      : (live?.unrealized_pnl_usd ?? 0)
    const settled = useHl ? (hl!.fills_count ?? sum.totalSettled) : (live?.settled_count ?? sum.totalSettled)
    return {
      key: m.key,
      label: m.label,
      family: m.family,
      realizedPnl,
      unrealizedPnl,
      open: useHl ? hl!.positions.length : (live?.open_count ?? 0),
      settled,
      wr: sum.wr,
      loaded: !!live,
    }
  })

  const activeModel = MODELS.find(m => m.key === active)!

  return (
    <div className="tab-section">
      {/* Cross-model leaderboard */}
      <div className="card">
        <div className="card__title card__title--with-actions">
          <span>Model leaderboard · classifica live</span>
          <span className="muted" style={{ fontSize: 11, fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
            click su un modello per vedere dettagli
          </span>
        </div>
        <ModelLeaderboard stats={stats} active={active} onPick={setActive} />
      </div>

      {/* Active model deep-dive */}
      <ModelPanel model={activeModel} />
    </div>
  )
}
