"use client";

import { useEffect, useState, FormEvent } from "react";
import { Activity, BarChart3, Bitcoin, ChevronDown, Download, Layers, Plus, Radio, Search, ShieldCheck, Wallet, X } from "lucide-react";
import { AreaChart, Area, ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid } from "recharts";

type Section = "stocks" | "crypto" | "timefolio";
type Market = { key: string; venue: string; symbol: string; section: string; asset_class: string; currency?: string; price: string | null; last_event_ms: number; observed_delay_ms?: number; gap_reason?: string; quote_kind?: string; book: null | { receive_ms: number; source_ms: number | null; bids: string[][]; asks: string[][] }; trades: { id: string; price: string; quantity: string; side: string; source_ms: number | null; receive_ms: number }[] };
type Strategy = { id: string; name: string; account: string; section: string; mode: string; currency: string; symbol?: string; status: string; running: boolean; updated_ms: number; realized_pnl: string | null; unrealized_pnl: string | null; pnl_basis?: string; };
type Account = { id: string; label: string; status: string; updated_ms: number; turnover_pct: string | null; realized_pnl: string | null; nav: string | null; data_note?: string; positions: { ticker: string; qty: number; weight_pct: number }[] };
type Order = { id: string; section: string; mode: string; strategy: string; account: string; symbol: string; venue: string; side: string; status: string; quantity?: string; weight_pct?: string; quantity_unit?: string; price?: string; price_unit?: string; created_ms: number; updated_ms: number };
type PnlRow = { key: string; id: string; strategy_id: string; account_scope: string; mode: string; currency: string; realized_pnl: string | null; unrealized_pnl: string | null; fees: string | null; funding: string | null; net_pnl: string | null; fee_convention: string; position_count: number | null; working_order_count: number | null; trades: number | null; asof_ms: number | null; mark_asof_ms: number | null; mark_provenance: string; attribution_status: string; status: string; runtime_status?: string; runtime_updated_ms?: number | null; unallocated_amount?: string | null; stale: boolean; evidence: string; equity_curve: { ts: number; net_pnl: string | null; status: string }[] };
type LatencyMetric = { stage: string; source_id: string; strategy_id: string; account_scope: string; mode: string; clock_domain: string; trace_ref: string | null; windows: Record<string, { p50_ms: number | null; p95_ms: number | null; p99_ms: number | null; n: number; coverage_ms: number }>; source_latest_age_ms: number | null; last_observation_age_ms: number | null; queue_depth: number | null; oldest_queue_age_ms: number | null; errors: number; drops: number; seen: number; sampled: number; sample_out: number; stale: boolean; effective_stale?: boolean; effective_source_age_ms?: number | null };
type LatencySnapshot = { generated_ms: number; process_id: string; clock_domain: string; metrics: LatencyMetric[]; strategies: unknown[]; resources: { process_cpu_seconds?: number | null; rss_bytes?: number | null; cgroup_cpu_seconds?: number | null; cgroup_memory_bytes?: number | null; cgroup_io_some_avg10?: number | null; cgroup_io_full_avg10?: number | null }; end_to_end_ms: null; end_to_end_status: string; snapshot_stale?: boolean };
type Observability = { pnl: PnlRow[]; latency: LatencySnapshot[]; latency_status: string; latency_error: string | null; native_status: string; native_error: string | null; etf_status: string; etf_error: string | null; end_to_end_ms: null; end_to_end_status: string };
type Snapshot = { generated_ms: number | null; received_ms?: number; connected?: boolean; markets: Market[]; strategies: Strategy[]; timefolio: Account[]; public_key?: JsonWebKey; observability?: Observability };
const titles = { stocks: "주식", crypto: "코인", timefolio: "타임폴리오" };
const names: Record<string, string> = { binance: "Binance", bitget: "Bitget", bybit: "Bybit", grvt: "GRVT", arcus: "Arcus", kis: "한국투자", kiwoom: "키움", toss: "토스", hyperliquid: "Hyperliquid", krx: "KRX", ls: "LS" };
const statusNames: Record<string, string> = { filled: "체결", Filled: "체결", New: "접수", created: "생성", accepted: "접수", submitted: "전송", Cancelled: "취소", rejected: "거절", Rejected: "거절", blocked: "차단", unknown: "미확인", PartiallyFilled: "부분 체결" };
const num = (v: unknown, max = 4) => v == null || v === "" ? "—" : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: max });
const pnl = (v: unknown) => v == null ? "—" : `${Number(v) > 0 ? "+" : ""}${num(v, 2)}`;
const sign = (v: unknown) => v == null || Number(v) === 0 ? "" : Number(v) > 0 ? "positive" : "negative";
const clock = (ms?: number | null) => ms ? new Date(ms).toLocaleTimeString("ko-KR", { timeZone: "Asia/Seoul", hour12: false }) : "—";
const datetime = (ms: number) => ms ? new Date(ms).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) : "시각 미확인";
const modeName = (mode: string) => ({ testnet: "TESTNET", sandbox: "TESTNET", live: "LIVE", real: "LIVE", timefolio: "TIMEFOLIO", paper: "PAPER", mock: "MOCK", mock2: "MOCK2", shadow: "SHADOW", unverified: "환경 미확인" }[mode] || mode || "환경 미확인");
const mergeOrders = (previous: Order[], incoming: Order[]) => [...new Map([...previous, ...incoming].map(o => [o.id, o])).values()]
  .sort((a, b) => b.updated_ms - a.updated_ms || b.id.localeCompare(a.id)).slice(0, 1000);
async function getJSON(path: string, signal?: AbortSignal): Promise<any> {
  const response = await fetch(path, { cache: "no-store", signal });
  if (!response.ok || !response.headers.get("content-type")?.includes("application/json")) throw new Error("데이터 연결을 확인해주세요.");
  return response.json();
}
async function encryptAccount(credentials: { username: string; password: string }, jwk: JsonWebKey) {
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSA-OAEP", hash: "SHA-256" }, false, ["encrypt"]);
  const secret = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["encrypt"]);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const [ciphertext, wrapped] = await Promise.all([
    crypto.subtle.encrypt({ name: "AES-GCM", iv }, secret, new TextEncoder().encode(JSON.stringify(credentials))),
    crypto.subtle.encrypt({ name: "RSA-OAEP" }, key, await crypto.subtle.exportKey("raw", secret)),
  ]);
  const b64 = (buffer: ArrayBuffer | Uint8Array) => btoa(String.fromCharCode(...new Uint8Array(buffer as ArrayBuffer)));
  return { version: 1, wrapped_key: b64(wrapped), iv: b64(iv), ciphertext: b64(ciphertext) };
}
function Empty({ children }: { children: React.ReactNode }) { return <div className="empty">{children}</div>; }
function Book({ market, now }: { market?: Market; now: number }) {
  const book = market?.book;
  const stale = !book || now - book.receive_ms > 5000;
  const maximum = Math.max(...(book ? [...book.bids, ...book.asks].map(x => Number(x[1])) : []), 1e-12);
  const spread = book?.asks[0] && book?.bids[0] ? Number(book.asks[0][0]) - Number(book.bids[0][0]) : null;
  const row = (level: string[], side: string, i: number) => <div key={`${side}-${i}`} className={`book-row ${side}`}><span className="depth" style={{ width: `${Math.min(100, Number(level[1]) / maximum * 100)}%` }} /><span>{num(level[0], 6)}</span><span>{num(level[1], 6)}</span><span>{num(Number(level[0]) * Number(level[1]), 2)}</span></div>;
  return <section className="panel book-panel"><div className="panel-head"><h2>호가창 <small>TOP 10</small></h2><span className={`tag ${stale ? "warn" : "live"}`}>{stale ? "수신 대기 / 지연" : "실시간"}</span></div><div className="book-label"><span>가격</span><span>수량</span><span>금액</span></div>{book ? <><div className={stale ? "stale-data" : ""}>{book.asks.slice(0, 10).reverse().map((x, i) => row(x, "ask", i))}</div><div className="mid-price"><strong>{num(market?.price, 6)}</strong><span>스프레드 {num(spread, 6)}</span></div><div className={stale ? "stale-data" : ""}>{book.bids.slice(0, 10).map((x, i) => row(x, "bid", i))}</div><div className="panel-foot">거래소 {clock(book.source_ms)} · 수신 {clock(book.receive_ms)}</div></> : <Empty>{market?.gap_reason ? "호가 동기화 중 · 완전한 스냅샷 수신 대기" : "호가 데이터 수신을 기다리고 있습니다."}</Empty>}</section>;
}
function Trades({ market }: { market?: Market }) {
  return <section className="panel trades-panel"><div className="panel-head"><h2>최근 체결</h2><span className="muted">최근 40건</span></div><div className="trade-row table-label"><span>가격</span><span>수량</span><span>시간 (KST)</span></div>{market?.trades.length ? <div className="trade-list">{market.trades.map(t => <div key={t.id} className="trade-row"><span className={t.side === "buy" ? "positive" : t.side === "sell" ? "negative" : ""}>{num(t.price, 6)}</span><span>{num(t.quantity, 6)}</span><span title={t.source_ms ? "거래소 시각" : "거래소 시각 미제공 · 수신 시각"}>{clock(t.source_ms || t.receive_ms)}{!t.source_ms && "*"}</span></div>)}</div> : <Empty>{market?.quote_kind === "bar_1m" ? "이 종목은 1분봉 수집 중입니다. 체결 스트림은 제공되지 않습니다." : "체결 데이터가 아직 없습니다."}</Empty>}</section>;
}

export default function Dashboard() {
  const [section, setSection] = useState<Section>("crypto");
  const [state, setState] = useState<Snapshot>({ generated_ms: null, markets: [], strategies: [], timefolio: [] });
  const [now, setNow] = useState(Date.now());
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("");
  const [venue, setVenue] = useState("all");
  const [search, setSearch] = useState("");
  const [mode, setMode] = useState("all");
  const [strategyId, setStrategyId] = useState("");
  const [orders, setOrders] = useState<Order[]>([]);
  const [orderFilter, setOrderFilter] = useState("all");
  const [period, setPeriod] = useState("all");
  const [history, setHistory] = useState<{ ts: number; realized_pnl: string }[]>([]);
  const [pnlKey, setPnlKey] = useState("");
  const [latencyWindow, setLatencyWindow] = useState<"1m" | "5m" | "15m">("15m");
  const [latencyProcess, setLatencyProcess] = useState("all");
  const [pnlHistory, setPnlHistory] = useState<{ ts: number; net_pnl: string | null; status: string }[]>([]);
  const [modal, setModal] = useState(false);
  const [pending, setPending] = useState(false);
  const [formError, setFormError] = useState("");
  const [registered, setRegistered] = useState<{ id: string; label: string }[]>([]);
  const [more, setMore] = useState(false);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let pollController: AbortController | undefined;
    let polling = false;
    const stopPolling = () => {
      polling = false;
      if (timer) clearTimeout(timer);
      pollController?.abort();
      pollController = undefined;
    };
    const poll = async () => {
      if (cancelled || !polling) return;
      pollController = new AbortController();
      try {
        const next = await getJSON("/api/state", pollController.signal);
        if (!cancelled && polling) { setState(next); setError(""); }
      } catch {
        if (!cancelled && polling) setError("연결이 끊겼습니다. 마지막 수신 데이터를 표시합니다.");
      }
      if (!cancelled && polling) timer = setTimeout(poll, document.hidden ? 5000 : 2000);
    };
    let source: EventSource | undefined;
    if (typeof EventSource !== "undefined") {
      try {
        source = new EventSource("/api/observability/stream");
        source.onopen = () => { stopPolling(); setError(""); };
        source.onmessage = event => {
          try {
            const next = JSON.parse(event.data) as Snapshot;
            if (!cancelled) { setState(next); setError(""); }
          } catch {
            if (!cancelled) setError("관측 스트림 데이터를 확인할 수 없습니다.");
          }
        };
        source.onerror = () => {
          if (!cancelled) {
            setError("스트림 연결을 재시도하고 있습니다. 마지막 수신 데이터를 표시합니다.");
            if (!polling) { polling = true; void poll(); }
          }
        };
      } catch {
        polling = true;
        void poll();
      }
    } else {
      polling = true;
      void poll();
    }
    const clockTimer = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      cancelled = true;
      source?.close();
      stopPolling();
      clearInterval(clockTimer);
    };
  }, []);
  useEffect(() => {
    let cancelled = false; let timer: ReturnType<typeof setTimeout>; setOrders([]); setMore(false); const controller = new AbortController();
    const poll = async () => { try { const result = await getJSON(`/api/orders?section=${section}`, controller.signal); if (!cancelled) { setOrders(previous => mergeOrders(previous, result.rows)); setMore(result.truncated); } } catch { } if (!cancelled) timer = setTimeout(poll, 10000); };
    poll(); return () => { cancelled = true; clearTimeout(timer); controller.abort(); };
  }, [section]);
  useEffect(() => { let cancelled = false; setHistory([]); if (strategyId) getJSON(`/api/pnl?strategy=${encodeURIComponent(strategyId)}`).then(x => { if (!cancelled) setHistory(x.rows); }).catch(() => {}); return () => { cancelled = true; }; }, [strategyId, state.generated_ms ? Math.floor(state.generated_ms / 60000) : 0]);
  useEffect(() => {
    let cancelled = false;
    setPnlHistory([]);
    if (pnlKey) getJSON(`/api/pnl?key=${encodeURIComponent(pnlKey)}`).then(x => {
      if (!cancelled) setPnlHistory(Array.isArray(x.rows) ? x.rows.slice(-1440) : []);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [pnlKey, state.generated_ms ? Math.floor(state.generated_ms / 60000) : 0]);
  const connected = !error && !!state.connected && !!state.received_ms && now - state.received_ms < 15000;
  const markets = state.markets.filter(m => m.section === section);
  const venues = [...new Set(markets.map(m => m.venue))];
  const filteredMarkets = markets.filter(m => (venue === "all" || m.venue === venue) && `${m.symbol} ${m.venue}`.toLowerCase().includes(search.toLowerCase()));
  const market = filteredMarkets.find(m => m.key === selected) || filteredMarkets[0];
  const strategies = state.strategies.filter(s => s.section === section && (mode === "all" || s.mode === mode));
  const strategy = strategies.find(s => s.id === strategyId) || strategies[0];
  const observability = state.observability;
  const pnlRows = [...(observability?.pnl || [])].sort((a, b) =>
    a.strategy_id.localeCompare(b.strategy_id) || a.mode.localeCompare(b.mode) ||
    a.account_scope.localeCompare(b.account_scope) || a.currency.localeCompare(b.currency));
  const activePnl = pnlRows.find(row => row.key === pnlKey) || pnlRows[0];
  const latencyProcesses = observability?.latency || [];
  const effectiveLatencyProcess = latencyProcesses.some(snapshot => snapshot.process_id === latencyProcess) ? latencyProcess : "all";
  const latencyMetrics = latencyProcesses.filter(snapshot => effectiveLatencyProcess === "all" || snapshot.process_id === effectiveLatencyProcess).flatMap(snapshot =>
    snapshot.metrics.map(metric => {
      const elapsed = Math.max(0, now - snapshot.generated_ms);
      const observationAge = metric.last_observation_age_ms === null ? null : metric.last_observation_age_ms + elapsed;
      const sourceAge = metric.source_latest_age_ms === null ? null : metric.source_latest_age_ms + elapsed;
      return { ...metric, process_id: snapshot.process_id, generated_ms: snapshot.generated_ms,
        effective_source_age_ms: sourceAge,
        effective_stale: !!snapshot.snapshot_stale || metric.stale || observationAge === null ||
          observationAge > 15000 || (sourceAge !== null && sourceAge > 15000) };
    })
  ).sort((a, b) => a.process_id.localeCompare(b.process_id) || a.stage.localeCompare(b.stage) ||
    a.source_id.localeCompare(b.source_id) || a.strategy_id.localeCompare(b.strategy_id)).slice(0, 128);
  const activeCount = strategies.filter(s => s.running && now - s.updated_ms < 45000).length;
  const freshCount = markets.filter(m => now - m.last_event_ms < 15000).length;
  const cutoff = period === "24h" ? now - 86400_000 : period === "7d" ? now - 7 * 86400_000 : 0;
  const filteredOrders = orders.filter(o => (mode === "all" || o.mode === mode) && (orderFilter === "all" || (orderFilter === "filled" ? o.status.toLowerCase() === "filled" : o.status.toLowerCase() !== "filled")) && o.created_ms >= cutoff);
  useEffect(() => { if (strategy?.id && strategy.id !== strategyId) setStrategyId(strategy.id); }, [strategy?.id]);
  useEffect(() => { if (activePnl?.key && activePnl.key !== pnlKey) setPnlKey(activePnl.key); }, [activePnl?.key]);
  const switchSection = (value: Section) => { setSection(value); setVenue("all"); setSearch(""); setSelected(""); setStrategyId(""); setMode("all"); };
  const exportOrders = () => {
    const safe = (value: unknown) => `"${String(value ?? "").replace(/^[=+@-]/, "'$&").replaceAll('"', '""')}"`;
    const csv = "\uFEFF" + [["시간(KST)", "계정", "전략", "환경", "거래소", "종목", "매수/매도", "가격", "수량", "상태", "주문ID"], ...filteredOrders.map(o => [datetime(o.created_ms), o.account, o.strategy, modeName(o.mode), o.venue, o.symbol, o.side, o.price, o.quantity, o.status, o.id])].map(row => row.map(safe).join(",")).join("\r\n");
    const href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" })); const a = document.createElement("a"); a.href = href; a.download = `vibetrading-${section}-orders.csv`; a.click(); URL.revokeObjectURL(href);
  };
  const addAccount = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setPending(true); setFormError(""); const form = event.currentTarget; const data = new FormData(form);
    try { if (!state.public_key) throw new Error("서버 연결 후 계정을 등록할 수 있습니다."); const envelope = await encryptAccount({ username: String(data.get("username")), password: String(data.get("password")) }, state.public_key); const response = await fetch("/api/accounts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label: data.get("label"), envelope }) }); const result = await response.json() as { error?: string }; if (!response.ok) throw new Error(result.error || "계정 등록 실패"); form.reset(); setRegistered((await getJSON("/api/accounts")).rows); setModal(false); } catch (e) { setFormError(e instanceof Error ? e.message : "계정 등록 실패"); } finally { setPending(false); }
  };
  const deleteAccount = async (id: string) => { setPending(true); try { const response = await fetch("/api/accounts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "delete", id }) }); if (!response.ok) throw new Error(); setRegistered(registered.filter(a => a.id !== id)); } catch { setFormError("계정 연결 해제에 실패했습니다."); } finally { setPending(false); } };
  const loadMore = async () => { if (!orders.length) return; const oldest = orders[orders.length - 1]; try { const result = await getJSON(`/api/orders?section=${section}&before=${oldest.updated_ms}&before_id=${encodeURIComponent(oldest.id)}`); setOrders(previous => mergeOrders(previous, result.rows)); setMore(result.truncated); } catch { setError("이전 주문 조회에 실패했습니다."); } };

  return <div className="shell"><aside className="sidebar"><a className="brand" href="/" aria-label="VibeTrading 홈"><span className="brand-mark"><Activity size={23} /></span><span>VIBE<span className="brand-light">TRADING</span></span></a><div className="sidebar-label">운용 모니터</div><nav aria-label="마켓 구분">{([["stocks", BarChart3], ["crypto", Bitcoin], ["timefolio", Layers]] as const).map(([key, Icon]) => <button key={key} onClick={() => switchSection(key)} className={`nav-item ${section === key ? "active" : ""}`} aria-current={section === key ? "page" : undefined}><Icon size={19} /><span>{titles[key]}</span>{section === key && <span className="nav-dot" />}</button>)}</nav><div className="sidebar-bottom"><ShieldCheck size={18} /><div>개인 운용 대시보드<small>조회 전용 · 주문 실행 없음</small></div></div></aside>
    <main className="workspace"><header className="topbar"><span className="breadcrumb">WORKSPACE <span>/</span> <strong>{titles[section]}</strong></span><div className="topbar-right"><span className="local-clock">{clock(now)} <small>KST</small></span><span className={`connection ${connected ? "on" : "off"}`}><span />{connected ? "서버 연결됨" : "서버 연결 대기"}</span></div></header><div className="content">
      <div className="page-heading"><div><div className="eyebrow">{section === "crypto" ? "CRYPTO MARKETS" : section === "stocks" ? "EQUITY MARKETS" : "ACCOUNT MONITOR"}</div><h1>{titles[section]} 운용 현황</h1><p>{section === "timefolio" ? "계정별 회전율, 전략과 실현 손익을 한곳에서 확인합니다." : "수집 종목의 시장 흐름과 전략 실행 결과를 확인합니다."}</p></div>{section === "timefolio" ? <button className="primary-button" onClick={() => { setModal(true); setFormError(""); getJSON("/api/accounts").then(x => setRegistered(x.rows.filter((a: { active: number }) => a.active))).catch(() => {}); }}><Plus size={16} />계정 연결</button> : <div className="heading-status"><Radio size={15} />2초 간격 갱신<small>마지막 수신 {clock(state.generated_ms)}</small></div>}</div>
      {(error || !connected) && <div className="notice" role="status">{error || "서버 데이터 연결을 기다리고 있습니다. 연결 후 실제 수집 데이터가 표시됩니다."}</div>}
      <section className="panel observability-panel" aria-label="시스템 상태 및 전략 손익">
        <div className="panel-head"><h2>시스템 상태 · 전략별 손익</h2><span className={`tag ${observability?.latency_status === "AVAILABLE" ? "live" : "warn"}`}>{observability?.latency_status === "AVAILABLE" ? "관측 연결됨" : observability?.latency_status === "NOT_CONFIGURED" ? "관측 미설정" : "관측 사용 불가"}</span></div>
        <div className="panel-foot">단계별 프로세스 구간만 표시 · 종단 간 지연 {observability?.end_to_end_status === "SEGMENTED" ? "측정 불가 (분할 관측)" : "미확인"} · 네이티브 원장 {observability?.native_status === "AVAILABLE" ? "연결됨" : observability?.native_status === "NOT_CONFIGURED" ? "미설정" : "사용 불가"} · KIS ETF {observability?.etf_status === "AVAILABLE" ? "연결됨 (모의 수수료 모델)" : observability?.etf_status === "NOT_CONFIGURED" ? "미설정" : "사용 불가"}{observability?.latency_error ? ` · 관측 ${observability.latency_error}` : ""}{observability?.native_error ? ` · 원장 ${observability.native_error}` : ""}{observability?.etf_error ? ` · ETF ${observability.etf_error}` : ""}</div>
        {latencyProcesses.length > 0 ? <><div className="order-tools"><label className="muted" htmlFor="latency-process">프로세스</label><select id="latency-process" aria-label="Latency 프로세스" value={effectiveLatencyProcess} onChange={event => setLatencyProcess(event.target.value)}><option value="all">전체 (최대 128개)</option>{latencyProcesses.map(snapshot => <option key={snapshot.process_id} value={snapshot.process_id}>{snapshot.process_id}</option>)}</select><label className="muted" htmlFor="latency-window">표시 구간</label><select id="latency-window" aria-label="Latency 표시 구간" value={latencyWindow} onChange={event => setLatencyWindow(event.target.value as "1m" | "5m" | "15m")}><option value="1m">1분</option><option value="5m">5분</option><option value="15m">15분</option></select></div>{latencyMetrics.length > 0 ? <div className="table-scroll"><table><thead><tr><th>단계</th><th>시계 구간</th><th className="right">p50 / p95 / p99 ms</th><th className="right">표본 / 커버리지</th><th className="right">큐 / 가장 오래된 항목</th><th className="right">오류 / 누락 / 수신 지연</th><th>상태</th></tr></thead><tbody>{latencyMetrics.map((metric, index) => { const window = metric.windows?.[latencyWindow]; return <tr key={`${metric.process_id}-${metric.stage}-${index}`}><td><strong>{metric.stage}</strong><small>{metric.strategy_id || metric.process_id}</small></td><td className="mono">{metric.clock_domain.slice(0, 20)}</td><td className="right mono">{num(window?.p50_ms, 2)} / {num(window?.p95_ms, 2)} / {num(window?.p99_ms, 2)}</td><td className="right mono">{num(window?.n, 0)} / {num(window?.coverage_ms, 0)} ms</td><td className="right mono">{num(metric.queue_depth, 0)} / {num(metric.oldest_queue_age_ms, 1)} ms</td><td className="right mono">{num(metric.errors, 0)} / {num(metric.drops, 0)} / {num(metric.effective_source_age_ms, 0)} ms</td><td><span className={`tag ${metric.effective_stale ? "warn" : "live"}`}>{metric.effective_stale ? "지연" : "정상"}</span></td></tr>; })}</tbody></table></div> : <Empty>선택한 프로세스에서 현재 latency 표본이 없습니다.</Empty>}<div className="panel-foot">{observability?.latency.filter(snapshot => effectiveLatencyProcess === "all" || snapshot.process_id === effectiveLatencyProcess).map(snapshot => <span key={snapshot.process_id}>{snapshot.process_id} · CPU 누적 {num(snapshot.resources?.process_cpu_seconds, 2)}초 · RSS {snapshot.resources?.rss_bytes == null ? "—" : `${num(snapshot.resources.rss_bytes / 1048576, 1)} MiB`} · cgroup CPU {num(snapshot.resources?.cgroup_cpu_seconds, 2)}초 · 메모리 {snapshot.resources?.cgroup_memory_bytes == null ? "—" : `${num(snapshot.resources.cgroup_memory_bytes / 1048576, 1)} MiB`} · I/O some/full avg10 {num(snapshot.resources?.cgroup_io_some_avg10, 2)} / {num(snapshot.resources?.cgroup_io_full_avg10, 2)} · </span>)}</div></> : <Empty>프로세스별 latency 자료가 수신되면 단계별 p50/p95/p99, 표본 수와 큐 상태가 표시됩니다.</Empty>}
        <div className="section-heading"><h2>전략 PnL <span>{pnlRows.length}개 스코프</span></h2><span className="muted">계정·모드·통화 분리</span></div>
        {pnlRows.length ? <><div className="strategy-panel">{pnlRows.map(row => <button key={row.key} className={`strategy-row ${activePnl?.key === row.key ? "selected" : ""}`} onClick={() => setPnlKey(row.key)}><span className={`strategy-dot ${row.runtime_status && /RUNNING|ACTIVE/i.test(row.runtime_status) ? "running" : ""}`} /><div><strong>{row.strategy_id}</strong><small>{row.account_scope} · {modeName(row.mode)} · {row.attribution_status} · {row.runtime_status || "실행 상태 미확인"}</small></div><span className="tag">{row.currency}</span><span className={`strategy-pnl ${sign(row.net_pnl)}`}>{pnl(row.net_pnl)}<small>순손익</small></span></button>)}</div>{activePnl && <><div className="account-metrics"><div><span>순손익</span><strong className={sign(activePnl.net_pnl)}>{pnl(activePnl.net_pnl)}<small>{activePnl.currency}</small></strong></div><div><span>실현 손익</span><strong className={sign(activePnl.realized_pnl)}>{pnl(activePnl.realized_pnl)}</strong></div><div><span>미실현 손익</span><strong className={sign(activePnl.unrealized_pnl)}>{pnl(activePnl.unrealized_pnl)}</strong></div><div><span>수수료 / 펀딩</span><strong>{pnl(activePnl.fees)} / {pnl(activePnl.funding)}</strong></div><div><span>포지션 / 작업 주문 / 거래</span><strong>{num(activePnl.position_count, 0)} / {num(activePnl.working_order_count, 0)} / {num(activePnl.trades, 0)}</strong></div></div><div className="panel-foot">{activePnl.fee_convention} · 기준 {datetime(activePnl.asof_ms || 0)} · 마크 {activePnl.mark_provenance} · {activePnl.status}</div>{pnlHistory.length > 1 && <div className="chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={pnlHistory.filter(item => item.net_pnl !== null)} margin={{ left: 0, right: 15 }}><CartesianGrid vertical={false} stroke="#263039" /><XAxis dataKey="ts" tickFormatter={t => clock(t).slice(0, 5)} stroke="#6f818c" minTickGap={60} tickLine={false} /><YAxis stroke="#6f818c" width={55} tickLine={false} tickFormatter={v => num(v, 2)} /><Tooltip labelFormatter={t => datetime(Number(t))} contentStyle={{ background: "#162129", border: "1px solid #34434d", borderRadius: 4 }} formatter={v => [pnl(v), activePnl.currency]} /><Area dataKey="net_pnl" stroke="#4ec7ad" fill="#4ec7ad" fillOpacity={0.08} strokeWidth={2} isAnimationActive={false} /></AreaChart></ResponsiveContainer></div>}</>}</> : <Empty>신뢰 가능한 원장 기준의 전략 손익 자료를 기다리고 있습니다.</Empty>}
      </section>
      {section !== "timefolio" && <><div className="metrics"><div className="metric"><span>수집 종목 / 거래소</span><strong>{markets.length}<small> / {venues.length}</small></strong><em>{freshCount}개 최근 수신</em></div><div className="metric"><span>작동 중인 전략</span><strong>{activeCount}<small>개</small></strong><em>{mode === "all" ? "전체 운용 환경" : modeName(mode)}</em></div><div className="metric"><span>선택 전략 실현 PnL</span><strong className={sign(strategy?.realized_pnl)}>{pnl(strategy?.realized_pnl)}<small>{strategy?.currency}</small></strong><em>{strategy?.pnl_basis || "성과 데이터 수신 대기"}</em></div><div className="metric"><span>선택 전략 미실현 PnL</span><strong className={sign(strategy?.unrealized_pnl)}>{pnl(strategy?.unrealized_pnl)}<small>{strategy?.currency}</small></strong><em>{strategy?.account || "계정 미선택"}</em></div></div>
        <div className="market-layout"><section className="panel watchlist"><div className="panel-head"><h2>수집 종목</h2><span className="count">{filteredMarkets.length}</span></div><div className="watchlist-tools"><label className="search"><Search size={15} /><input aria-label="종목 검색" placeholder="종목 검색" value={search} onChange={e => setSearch(e.target.value)} /></label><select aria-label="거래소 필터" value={venue} onChange={e => setVenue(e.target.value)}><option value="all">전체 거래소</option>{venues.map(v => <option key={v} value={v}>{names[v] || v}</option>)}</select></div><div className="watchlist-scroll">{filteredMarkets.length ? filteredMarkets.map(m => <button className={`watch-row ${market?.key === m.key ? "selected" : ""}`} key={m.key} onClick={() => setSelected(m.key)}><span className="symbol-icon">{m.symbol.replace(/.*:/, "").slice(0, 1)}</span><span className="watch-symbol"><strong>{m.symbol.replace(/USDT$/, "/USDT")}</strong><small>{names[m.venue] || m.venue} · {m.asset_class === "crypto_perp" ? "무기한" : m.asset_class}</small></span><span className="watch-price">{num(m.price, 4)}<small className={now - m.last_event_ms < 15000 ? "positive" : "muted"}>{now - m.last_event_ms < 15000 ? "수신 중" : "수신 지연"}</small></span></button>) : <Empty>{section === "stocks" ? "장중 수집 데이터가 도착하면 종목이 표시됩니다." : "거래소 데이터를 기다리고 있습니다."}</Empty>}</div><div className="panel-foot">거래소별 동일 종목을 별도로 표시</div></section><div className="market-detail"><div className="instrument-bar"><div><strong>{market?.symbol || "종목 선택"}</strong><span>{market ? names[market.venue] || market.venue : "—"}</span></div><div><span className="muted">최근 가격</span><strong>{num(market?.price, 6)}</strong><small>{market?.currency || ""}</small></div></div>{market?.observed_delay_ms != null && market.observed_delay_ms > 5000 && <div className="notice compact">공급사 지연 데이터 · 관측 지연 {num(market.observed_delay_ms / 1000, 1)}초</div>}<div className="book-trade-grid"><Book market={market} now={now} /><Trades market={market} /></div></div></div></>}
      {section === "timefolio" && <><div className="account-overview"><Wallet size={22} /><div><strong>{state.timefolio.length}개 계정</strong><span>계정 데이터는 2분 간격으로 조회합니다.</span></div><span className="tag">KRW</span></div>{state.timefolio.length ? <div className="account-grid">{state.timefolio.map(account => { const active = state.strategies.filter(s => s.account === account.id && s.running && now - s.updated_ms < 45000); return <section className="panel account-panel" key={account.id}><div className="panel-head"><h2>{account.label}</h2><span className={`tag ${account.status === "connected" && now - account.updated_ms < 300000 ? "live" : "warn"}`}>{({ connected: now - account.updated_ms < 300000 ? "연결됨" : "갱신 지연", loading: "조회 중", login_failed: "로그인 실패", unavailable: "조회 실패" }[account.status] || "대기")}</span></div><div className="account-metrics"><div><span>회전율</span><strong>{num(account.turnover_pct, 2)}{account.turnover_pct != null && <small>%</small>}</strong></div><div><span>실현 PnL</span><strong className={sign(account.realized_pnl)}>{pnl(account.realized_pnl)}<small>KRW</small></strong></div><div><span>NAV</span><strong>{num(account.nav, 0)}</strong></div></div><div className="account-strategies"><span className="muted">작동 중인 전략</span><strong>{active.length ? active.map(s => s.name).join(", ") : "작동 확인된 전략 없음"}</strong></div><div className="holdings">{account.positions.map(p => <div key={p.ticker}><span>{p.ticker}</span><span>{num(p.qty, 0)}주</span><span>{num(p.weight_pct, 2)}%</span></div>)}</div><div className="panel-foot">{clock(account.updated_ms)} 조회 · {account.data_note ? account.data_note + " · " : ""}— 표시는 원본 지표 미확인</div></section>; })}</div> : <section className="panel"><Empty>계정을 연결하면 회전율, 실현 손익과 보유 종목이 표시됩니다.</Empty></section>}</>}
      <div className="section-heading"><h2>전략 모니터 <span>PILOT TRADING</span></h2><select aria-label="운용 환경" value={mode} onChange={e => setMode(e.target.value)}><option value="all">전체 환경</option><option value="testnet">Testnet</option><option value="live">실거래</option><option value="shadow">Shadow</option><option value="paper">Paper</option><option value="timefolio">Timefolio</option><option value="unverified">환경 미확인</option></select></div>
      <div className="strategy-layout"><section className="panel strategy-panel"><div className="panel-head"><h2>전략 · 계정</h2><span className="count">{strategies.length}</span></div>{strategies.length ? strategies.map(s => <button key={s.id} className={`strategy-row ${strategy?.id === s.id ? "selected" : ""}`} onClick={() => setStrategyId(s.id)}><span className={`strategy-dot ${s.running && now - s.updated_ms < 45000 ? "running" : ""}`} /><div><strong>{s.name}</strong><small>{s.account} · {s.symbol || s.id.slice(0, 20)}</small></div><span className={`tag ${s.mode === "live" ? "warn" : ""}`}>{modeName(s.mode)}</span><span className={`strategy-pnl ${sign(s.realized_pnl)}`}>{pnl(s.realized_pnl)}<small>{s.currency}</small></span></button>) : <Empty>실행 기록 또는 전략 상태가 수신되면 표시됩니다.</Empty>}</section><section className="panel chart-panel"><div className="panel-head"><h2>실현 PnL 추이</h2><span className="muted">최근 24시간 · {strategy?.currency || "—"}</span></div><div className="chart-summary"><strong className={sign(strategy?.realized_pnl)}>{pnl(strategy?.realized_pnl)}</strong><span>{strategy?.name || "전략 미선택"}</span></div>{history.length > 1 ? <div className="chart"><ResponsiveContainer width="100%" height="100%"><AreaChart data={history.map(x => ({ ts: x.ts, pnl: Number(x.realized_pnl) }))} margin={{ left: 0, right: 15 }}><CartesianGrid vertical={false} stroke="#263039" /><XAxis dataKey="ts" tickFormatter={t => clock(t).slice(0, 5)} stroke="#6f818c" minTickGap={60} tickLine={false} /><YAxis stroke="#6f818c" width={55} tickLine={false} tickFormatter={v => num(v, 2)} /><Tooltip labelFormatter={t => datetime(Number(t))} contentStyle={{ background: "#162129", border: "1px solid #34434d", borderRadius: 4 }} formatter={v => [pnl(v), strategy?.currency || "PnL"]} /><Area dataKey="pnl" stroke="#4ec7ad" fill="#4ec7ad" fillOpacity={0.08} strokeWidth={2} isAnimationActive={false} /></AreaChart></ResponsiveContainer></div> : <Empty>성과 관측치가 쌓이면 그래프가 표시됩니다.<small>통화·계정·전략이 다른 손익은 합산하지 않습니다.</small></Empty>}</section></div>
      <section className="panel order-panel"><div className="panel-head orders-heading"><h2>주문 이력 <span className="count">{filteredOrders.length}</span></h2><div className="order-tools"><select aria-label="주문 상태" value={orderFilter} onChange={e => setOrderFilter(e.target.value)}><option value="all">전체 상태</option><option value="filled">체결</option><option value="other">미체결 / 기타</option></select><select aria-label="주문 기간" value={period} onChange={e => setPeriod(e.target.value)}><option value="24h">24시간</option><option value="7d">7일</option><option value="all">전체 기록</option></select><button className="quiet-button" onClick={exportOrders} disabled={!filteredOrders.length}><Download size={14} />CSV</button></div></div><div className="table-scroll"><table><thead><tr><th>시간 (KST)</th><th>전략 / 계정</th><th>환경</th><th>거래소 · 종목</th><th>방향</th><th className="right">가격</th><th className="right">수량 / 비중</th><th>상태</th></tr></thead><tbody>{filteredOrders.map(o => <tr key={o.id}><td className="mono" title={o.id}>{datetime(o.created_ms)}</td><td><strong>{o.strategy}</strong><small>{o.account}</small></td><td><span className={`tag ${o.mode === "live" ? "warn" : ""}`}>{modeName(o.mode)}</span></td><td><strong>{o.symbol}</strong><small>{names[o.venue] || o.venue}</small></td><td><span className={o.side.toLowerCase() === "buy" ? "positive" : "negative"}>{o.side.toLowerCase() === "buy" ? "매수" : "매도"}</span></td><td className="right mono">{num(o.price, 6)}{o.price_unit && <small>{o.price_unit}</small>}</td><td className="right mono">{num(o.quantity ?? o.weight_pct, 6)}{o.quantity_unit || (o.quantity == null && o.weight_pct != null ? "%" : "")}</td><td><span className="order-status">{statusNames[o.status] || o.status}</span></td></tr>)}</tbody></table></div>{!filteredOrders.length && <Empty>선택한 조건에 해당하는 주문 기록이 없습니다.</Empty>}{more && <div className="load-more"><button className="quiet-button" onClick={loadMore}>이전 주문 불러오기 <ChevronDown size={14} /></button></div>}<div className="panel-foot">실제 주문·운용 기록 기반 · 주문 상태는 체결과 구분됩니다. · CSV는 현재 조회된 기록을 내보냅니다.</div></section>
      <footer className="footer"><span>VIBETRADING <span className="muted">/ OBSERVATORY</span></span><span>조회 전용 <span>·</span> Asia/Seoul</span></footer>
    </div></main>
    {modal && <div className="modal-backdrop" onClick={e => { if (e.target === e.currentTarget && !pending) setModal(false); }}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="account-title"><div className="panel-head"><h2 id="account-title">타임폴리오 계정 연결</h2><button className="icon-button" disabled={pending} aria-label="닫기" onClick={() => setModal(false)}><X size={18} /></button></div><p>계정별 회전율, 실현 손익과 보유 종목을 조회합니다.</p><form onSubmit={addAccount}><label>표시 이름<input name="label" required maxLength={50} placeholder="예: 국내주식 파일럿" autoFocus /></label><label>타임폴리오 로그인 ID<input name="username" required autoComplete="username" maxLength={200} /></label><label>비밀번호<input name="password" type="password" required autoComplete="current-password" maxLength={200} /></label><div className="privacy-note"><ShieldCheck size={16} />로그인 정보는 브라우저에서 암호화되며, 거래 서버에서만 복호화됩니다. 계정 연결로 주문은 실행되지 않습니다.</div>{formError && <div className="notice" role="alert">{formError}</div>}<button className="primary-button full" disabled={pending || !connected || !state.public_key}>{pending ? "연결 중…" : "계정 연결"}</button></form>{registered.length > 0 && <div className="registered"><span className="muted">등록한 계정</span>{registered.map(a => <div key={a.id}><span>{a.label}</span><button className="quiet-button" disabled={pending} onClick={() => deleteAccount(a.id)}>연결 해제</button></div>)}</div>}</section></div>}
  </div>;
}
