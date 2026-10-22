from __future__ import annotations

import os
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


TMS_ERROR_RE = re.compile(r"\[TMS\]\s*오류\s*\n+\s*([^\n]{2,200})")
CONFIRM_TRIES = 5
CONFIRM_WAIT_MS = 1500


@dataclass(frozen=True)
class TimefolioCredentials:
    username: str
    password: str


@dataclass(frozen=True)
class TimefolioStatus:
    logged_in: bool
    url: str
    title: str
    has_order_screen: bool
    result: str = ""


@dataclass(frozen=True)
class TimefolioOrderIntent:
    account_id: str
    ticker: str
    side: str
    weight_pct: Decimal
    opp_tick: int = 1
    submit: bool = False


@dataclass(frozen=True)
class TimefolioOrderResult:
    status: str
    ticker: str
    side: str
    weight_pct: Decimal
    result: str
    filled: bool = False
    accepted: bool = False
    pending: bool = False


class AsyncTimefolioBrowserManager:
    def __init__(self, *, headless: bool = True, live_submit_enabled: bool = False) -> None:
        self.headless = headless
        self.live_submit_enabled = live_submit_enabled
        self._pw: Any = None
        self._browser: Any = None
        self._contexts: dict[str, Any] = {}
        self._pages: dict[str, Any] = {}

    async def __aenter__(self) -> "AsyncTimefolioBrowserManager":
        if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
            from vibetrading.settings import get_settings

            browser_path = get_settings().data_root / "playwright-browsers"
            browser_path.mkdir(parents=True, exist_ok=True)
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_path)
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("playwright is required for Timefolio automation. Install requirements and run playwright install chromium.") from exc
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless, args=["--no-sandbox"])
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        for context in list(self._contexts.values()):
            try:
                await context.close()
            except Exception:
                pass
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._contexts.clear()
        self._pages.clear()
        self._browser = None
        self._pw = None

    async def page_for(self, account_id: str) -> Any:
        if self._browser is None:
            raise RuntimeError("AsyncTimefolioBrowserManager must be used as an async context manager")
        page = self._pages.get(account_id)
        if page is not None and not page.is_closed():
            return page
        context = await self._browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul")
        page = await context.new_page()
        self._contexts[account_id] = context
        self._pages[account_id] = page
        return page

    async def login(self, account_id: str, creds: TimefolioCredentials) -> TimefolioStatus:
        page = await self.page_for(account_id)
        last = TimefolioStatus(False, page.url, "", False, "not_attempted")
        for _ in range(3):
            try:
                await page.goto("https://contest.timefolio.net/", wait_until="domcontentloaded", timeout=30000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                email = page.locator("#email, input[type=email]").first
                await email.wait_for(state="visible", timeout=15000)
                password = page.locator("#password, input[type=password]").first
                await password.wait_for(state="visible", timeout=5000)
                await email.fill(creds.username)
                await password.fill(creds.password)
                await page.get_by_role("button", name=re.compile("submit|로그인|login", re.I)).click(timeout=5000)
                try:
                    await page.wait_for_function(
                        "() => { const t = document.body.innerText || ''; return /logout/i.test(t) && t.includes('주문'); }",
                        timeout=15000,
                    )
                except Exception:
                    pass
                last = await self.status(account_id)
                if last.logged_in:
                    return last
            except Exception as exc:
                last = TimefolioStatus(False, page.url, "", False, str(exc))
            await page.wait_for_timeout(1500)
        return last

    async def status(self, account_id: str) -> TimefolioStatus:
        page = await self.page_for(account_id)
        text = await self._body_text(page)
        return TimefolioStatus(
            logged_in="logout" in text.lower() and "주문" in text,
            url=page.url,
            title=await page.title(),
            has_order_screen="신규 주문" in text or "주문" in text,
            result="ok",
        )

    async def ensure_logged_in(self, account_id: str, creds: TimefolioCredentials) -> TimefolioStatus:
        status = await self.status(account_id)
        if status.logged_in:
            return status
        return await self.login(account_id, creds)

    async def prepare_order(self, account_id: str, intent: TimefolioOrderIntent) -> TimefolioOrderResult:
        page = await self.page_for(account_id)
        ticker = str(intent.ticker or "").strip().zfill(6)
        side = str(intent.side or "").lower()
        weight_pct = max(Decimal("0.01"), min(Decimal("100"), intent.weight_pct))
        opp_tick = max(1, min(10, int(intent.opp_tick or 1)))
        if not ticker.strip("0") or side not in {"buy", "sell"}:
            return TimefolioOrderResult("REJECTED", ticker, side, weight_pct, "invalid Timefolio order payload")
        if intent.submit and not self.live_submit_enabled:
            return TimefolioOrderResult("REJECTED", ticker, side, weight_pct, "dry-run: Timefolio submit disabled")
        before_summary = await self.scrape_summary(page)
        await self._open_order_ticket(page)
        await self._select_ticker(page, ticker)
        await self._choose_side(page, side)
        await self._fill_weight(page, weight_pct)
        await self._choose_default_price_type(page, opp_tick=opp_tick)
        if not intent.submit:
            return TimefolioOrderResult(
                "ACCEPTED",
                ticker,
                side,
                weight_pct,
                "prepared Timefolio order form without submit",
                accepted=False,
            )
        await self._submit_order(page)
        await page.wait_for_timeout(1800)
        try:
            await page.wait_for_load_state("networkidle", timeout=7000)
        except Exception:
            pass
        tms = await self.detect_and_close_tms_error(page)
        if tms:
            await self._ensure_no_dialog(page)
            return TimefolioOrderResult("REJECTED", ticker, side, weight_pct, f"[TMS] 오류: {tms}")
        summary = await self.scrape_summary(page)
        await self._ensure_no_dialog(page)
        filled = self._position_changed(before_summary, summary, ticker=ticker, side=side)
        working = await self._working_order_exists(page, ticker)
        for _ in range(CONFIRM_TRIES):
            if filled or working:
                break
            await page.wait_for_timeout(CONFIRM_WAIT_MS)
            summary = await self.scrape_summary(page)
            filled = self._position_changed(before_summary, summary, ticker=ticker, side=side)
            working = await self._working_order_exists(page, ticker)
        if filled:
            return TimefolioOrderResult("FILLED", ticker, side, weight_pct, f"{ticker} {side} filled", filled=True, accepted=True)
        if working:
            return TimefolioOrderResult("ACCEPTED", ticker, side, weight_pct, f"{ticker} {side} accepted pending", accepted=True, pending=True)
        return TimefolioOrderResult("UNKNOWN", ticker, side, weight_pct, f"{ticker} {side} unknown after limited confirmation")

    async def scrape_summary(self, page: Any | None = None) -> dict[str, Any]:
        page = page or next(iter(self._pages.values()))
        text = await self._body_text(page)
        return await page.evaluate(
            r"""(bodyText) => {
                const norm = s => String(s || '').replace(/\s+/g, ' ').trim();
                const tables = [...document.querySelectorAll('table')].map((table, ti) => ({
                  index: ti,
                  text: norm(table.innerText),
                  rows: [...table.querySelectorAll('tr')].map(tr => [...tr.children].map(td => norm(td.innerText || td.textContent)))
                })).filter(x => x.text);
                const toNum = value => {
                  const s = String(value || '').replace(/,/g, '').replace(/[^0-9.\-]/g, '');
                  const n = Number(s);
                  return Number.isFinite(n) ? n : 0;
                };
                const allText = norm(bodyText || document.body.innerText || '');
                const nav = /NAV\s*([0-9,]+(?:\.[0-9]+)?)/.exec(allText);
                const positions = [];
                const holdingsTable = tables.find(t => /잔고\s*\/\s*비중\s*\/\s*평가액/.test(t.text));
                if (holdingsTable) {
                  for (const row of holdingsTable.rows) {
                    const code = String(row[0] || '').replace(/^A/, '');
                    if (!/^[0-9A-Z]{6}$/.test(code)) continue;
                    positions.push({ticker: code, qty: Math.trunc(toNum(row[4])), weight_pct: toNum(row[5])});
                  }
                }
                return {text: allText.slice(0, 3000), nav_text: nav ? nav[1] : '', positions, tables};
            }""",
            text,
        )

    async def detect_and_close_tms_error(self, page: Any) -> str | None:
        visible_text = ""
        try:
            dialogs = page.locator('[role="dialog"]:visible')
            count = min(await dialogs.count(), 4)
            visible_text = "\n".join([await dialogs.nth(i).inner_text(timeout=1000) for i in range(count)])
        except Exception:
            pass
        match = TMS_ERROR_RE.search(visible_text) or TMS_ERROR_RE.search(await self._body_text(page))
        if not match:
            return None
        try:
            await self._click_first_visible(page.get_by_role("button", name=re.compile(r"^확인$")), timeout=1000)
            await page.wait_for_timeout(300)
        except Exception:
            pass
        return match.group(1).strip()

    async def _open_order_ticket(self, page: Any) -> None:
        await self._ensure_no_dialog(page)
        body = await self._body_text(page)
        if "대회가 종료되었습니다" in body:
            raise RuntimeError("CONTEST_CLOSED_OR_ORDER_DISABLED")
        if await self._click_first_visible(page.get_by_role("button", name=re.compile(r"신규\s*주문")), timeout=2500):
            await page.wait_for_timeout(700)
            await page.locator('[role="dialog"]').last.wait_for(state="visible", timeout=5000)
            return
        await page.reload(wait_until="domcontentloaded", timeout=20000)
        await self._ensure_no_dialog(page)
        body = await self._body_text(page)
        if "대회가 종료되었습니다" in body:
            raise RuntimeError("CONTEST_CLOSED_OR_ORDER_DISABLED")
        if await self._click_first_visible(page.get_by_role("button", name=re.compile(r"신규\s*주문")), timeout=3000):
            await page.wait_for_timeout(700)
            await page.locator('[role="dialog"]').last.wait_for(state="visible", timeout=5000)
            return
        raise RuntimeError("Timefolio new-order button was not found")

    async def _ensure_no_dialog(self, page: Any) -> None:
        for _ in range(3):
            try:
                if await page.locator('[role="dialog"]').count() == 0:
                    return
                await self._close_ticket(page)
                await page.wait_for_timeout(250)
            except Exception:
                return

    async def _select_ticker(self, page: Any, ticker: str) -> None:
        dialog = page.locator('[role="dialog"]').last
        try:
            field = dialog.locator('input[placeholder="종목 선택"]').first
            await field.wait_for(state="visible", timeout=2500)
        except Exception:
            field = dialog.locator('input[type="text"]').nth(1)
        await field.fill(ticker)
        await page.wait_for_timeout(900)
        code_re = re.compile(rf"(?:^|\[)A?{re.escape(ticker)}(?:\]|\s|$)")
        candidates = page.locator("li:visible").filter(has_text=code_re)
        if await self._click_first_visible(candidates, timeout=1200):
            await page.wait_for_timeout(700)
        else:
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(700)
        text = await dialog.inner_text(timeout=3000)
        if ticker not in text and f"A{ticker}" not in text:
            raise RuntimeError(f"Timefolio ticker selection failed: {ticker}")
        if "종목 미선택" in text:
            raise RuntimeError(f"Timefolio ticker candidate was not selected: {ticker}")

    async def _choose_side(self, page: Any, side: str) -> None:
        dialog = page.locator('[role="dialog"]').last
        value = "true" if side == "buy" else "false"
        radio = dialog.locator(f'input[name="매수도"][value="{value}"]').first
        radio_id = await radio.get_attribute("id")
        label = dialog.locator(f'label[for="{radio_id}"]').first if radio_id else None
        if label is not None and await label.count():
            try:
                await label.click(timeout=2500)
            except Exception:
                await label.click(force=True, timeout=2500)
        else:
            await radio.check(force=True, timeout=2500)
        await page.wait_for_timeout(300)
        checked = await radio.evaluate("el => !!el.checked")
        opposite_value = "false" if side == "buy" else "true"
        opposite = dialog.locator(f'input[name="매수도"][value="{opposite_value}"]').first
        opposite_checked = await opposite.evaluate("el => !!el.checked") if await opposite.count() else False
        if not checked or opposite_checked:
            raise RuntimeError("Timefolio side radio verification failed")

    async def _fill_weight(self, page: Any, weight_pct: Decimal) -> None:
        dialog = page.locator('[role="dialog"]').last
        number_inputs = dialog.locator('input[type="number"]')
        if await number_inputs.count() < 1:
            raise RuntimeError("Timefolio weight input was not found")
        value = f"{weight_pct:.2f}".rstrip("0").rstrip(".")
        await number_inputs.nth(0).fill(value)
        got = await number_inputs.nth(0).evaluate("el => el.value")
        if abs(float(got) - float(weight_pct)) > 0.011:
            raise RuntimeError(f"Timefolio weight verification failed: input={value}, got={got!r}")

    async def _choose_default_price_type(self, page: Any, *, opp_tick: int) -> None:
        dialog = page.locator('[role="dialog"]').last
        await dialog.locator('input[name="prcTy"][value="Opp"]').first.check(force=True, timeout=2000)
        if not await dialog.locator('input[name="prcTy"][value="Opp"]').first.evaluate("el => !!el.checked"):
            raise RuntimeError("Timefolio Opp price type was not selected")
        nums = dialog.locator('input[type="number"]')
        if await nums.count() < 2:
            raise RuntimeError("Timefolio Opp tick input was not found")
        await nums.nth(1).fill(str(opp_tick))
        got = await nums.nth(1).evaluate("el => el.value")
        if str(got).strip() != str(opp_tick):
            raise RuntimeError(f"Timefolio Opp tick verification failed: input={opp_tick}, got={got!r}")
        try:
            await dialog.locator('input[name="isSlice"][value="false"]').first.check(force=True, timeout=1200)
        except Exception:
            pass

    async def _submit_order(self, page: Any) -> None:
        dialog = page.locator('[role="dialog"]').last
        if not await self._click_first_visible(dialog.get_by_role("button", name=re.compile(r"주문\s*제출")), timeout=2500):
            raise RuntimeError("Timefolio submit button was not found")
        for pattern in (r"^확인$", r"^예$", r"^동의$"):
            if await self._click_first_visible(page.get_by_role("button", name=re.compile(pattern)), timeout=700):
                await page.wait_for_timeout(500)
                return

    async def _close_ticket(self, page: Any) -> None:
        for pattern in (r"닫기|Close", r"^확인$"):
            if await self._click_first_visible(page.get_by_role("button", name=re.compile(pattern)), timeout=1000):
                await page.wait_for_timeout(300)
                return
        await page.keyboard.press("Escape")

    async def _working_order_exists(self, page: Any, ticker: str) -> bool:
        rows = await page.evaluate(
            r"""(ticker) => {
                const norm = s => String(s || '').replace(/\s+/g, ' ').trim();
                for (const t of document.querySelectorAll('table')) {
                    const trs = t.querySelectorAll('tr');
                    const head = norm([trs[0], trs[1]].map(r => (r ? r.innerText : '')).join(' '));
                    if (!(/미체결/.test(head) && /타겟/.test(head))) continue;
                    for (const tr of t.querySelectorAll('tr')) {
                        const cells = [...tr.children].map(td => norm(td.innerText));
                        if (!cells.some(c => c === `A${ticker}` || c === ticker)) continue;
                        const btn = [...tr.querySelectorAll('button')].find(b => /^-?[0-9.]+$/.test(norm(b.innerText)));
                        if (btn) return true;
                    }
                }
                return false;
            }""",
            ticker,
        )
        return bool(rows)

    async def _click_first_visible(self, locator: Any, *, timeout: int = 1200) -> bool:
        try:
            count = min(await locator.count(), 12)
        except Exception:
            return False
        for index in range(count):
            item = locator.nth(index)
            try:
                if await item.is_visible(timeout=timeout) and await item.is_enabled(timeout=timeout):
                    await item.click(timeout=timeout)
                    return True
            except Exception:
                continue
        return False

    async def _body_text(self, page: Any) -> str:
        try:
            return await page.locator("body").inner_text(timeout=5000)
        except Exception:
            return ""

    @staticmethod
    def _position_changed(before: dict[str, Any], after: dict[str, Any], *, ticker: str, side: str) -> bool:
        def qty(summary: dict[str, Any]) -> int:
            for pos in summary.get("positions") or []:
                if str(pos.get("ticker") or "").zfill(6) == ticker:
                    return int(pos.get("qty") or 0)
            return 0

        before_qty = qty(before or {})
        after_qty = qty(after or {})
        return after_qty > before_qty if side == "buy" else after_qty < before_qty

