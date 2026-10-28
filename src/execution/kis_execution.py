"""KIS domestic cash orders, with explicit real/mock credential namespaces.

TR IDs follow koreainvestment/open-trading-api official domestic_stock samples.
Mutating calls are made once; an ambiguous transport result is never retried.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import aiohttp

from core.models import AssetClass, OrderIntent, Side
from feeds.providers.kis_client import KisAuthClient
from runtime.settings import RuntimeSettings

KST = timezone(timedelta(hours=9))


class KisOrderRejected(RuntimeError):
    """The broker explicitly rejected a request; no accepted order exists."""

    def __init__(self,message,*,code='',business_message=''):
        super().__init__(message)
        self.code,self.business_message=str(code),business_message


def account_permission_rejection(exc):
    """Narrow broker permission failures; never infer fills from market errors."""
    message=getattr(exc,'business_message','') or str(exc)
    code=getattr(exc,'code','')
    return (code=='40070000' and '매매불가 종목' in message) or any(
        text in message for text in ('거래권한','매매권한','거래약정 미등록','파생ETF거래 미신청'))


class KisCashBroker:
    name = "kis"
    execution_permit_protocol = 1

    def __init__(self, settings: RuntimeSettings | None = None, *, mode: str = "mock"):
        self.client = KisAuthClient(settings, mode=mode)
        self.mode = mode
        self._common_owner_bound = False
        self._lock = asyncio.Lock()
        self._next_request_at = 0.

    def account_params(self) -> dict[str, str]:
        values = self.client.provider.values
        if values.get('KIS_ACCOUNT_PRODUCT_CODE') != '01':
            raise RuntimeError('ETF cash orders require a KIS domestic stock account product 01')
        if not values.get('KIS_ACCOUNT_NUMBER'):
            raise RuntimeError('KIS cash account is not configured')
        return {'CANO': values['KIS_ACCOUNT_NUMBER'], 'ACNT_PRDT_CD': '01'}

    @property
    def authority_check(self):
        return getattr(self,'_authority_check',None)

    @authority_check.setter
    def authority_check(self, check):
        if hasattr(self,'_authority_check') or not callable(check):
            raise RuntimeError('KIS account authority must be callable and bound once')
        self._authority_check = check

    def tr(self, suffix: str) -> str:
        return ('VTTC' if self.mode in {'mock','mock2'} else 'TTTC') + suffix

    async def connect(self):
        self.account_params()
        await self.client.issue_access_token()

    async def request(self, method: str, endpoint: str, tr_id: str, data: dict[str, str], *, tr_cont: str = '', permit=None) -> dict[str, Any]:
        # The mock endpoint permits only a small request rate. Serialize all
        # calls from this broker, including reconciliation and balance reads.
        async with self._lock:
            await asyncio.sleep(max(0.,self._next_request_at-time.monotonic()))
            self._next_request_at = time.monotonic()+.55
            if method == 'GET':
                response = await self.client.get('/uapi/domestic-stock/v1/trading/' + endpoint,
                    tr_id=tr_id, params=data, tr_cont=tr_cont)
            else:
                if not callable(getattr(self,'authority_check',None)):
                    raise RuntimeError('KIS mutation requires account authority')
                token, _ = await self.client.issue_access_token()
                headers = self.client._base_headers() | {'authorization': 'Bearer ' + token,
                    'appkey': self.client._app_key(), 'appsecret': self.client._app_secret(),
                    'tr_id': tr_id, 'custtype': 'P'}
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                    authority = getattr(self,'authority_check',None)
                    if not callable(authority):
                        raise RuntimeError('KIS mutation requires account authority')
                    for check in (authority,permit):
                        if check is None: continue
                        result = check()
                        if inspect.isawaitable(result):
                            if inspect.iscoroutine(result):
                                result.close()
                            raise TypeError('KIS dispatch permit must be synchronous')
                        if result is False:
                            raise RuntimeError('KIS dispatch permit denied')
                    async with session.post(self.client.endpoints.rest_base_url + '/uapi/domestic-stock/v1/trading/' + endpoint,
                                            json=data, headers=headers) as result:
                        body = await self.client._safe_json(result)
                        if body.get('rt_cd') not in (None, '0', 0):
                            raise KisOrderRejected(self.client._format_error(tr_id, result.status, body),
                                code=body.get('msg_cd',''),business_message=str(body.get('msg1','')))
                        if result.status != 200 or str(body.get('rt_cd')) != '0':
                            raise RuntimeError('KIS order result is ambiguous; reconcile before another submission')
                        response = {'body': body, 'headers': dict(result.headers)}
            return response

    async def account_state(self) -> dict[str, Any]:
        params = self.account_params() | {'AFHR_FLPR_YN': 'N', 'OFL_YN': '', 'INQR_DVSN': '02',
            'UNPR_DVSN': '01', 'FUND_STTL_ICLD_YN': 'N', 'FNCG_AMT_AUTO_RDPT_YN': 'N',
            'PRCS_DVSN': '00', 'CTX_AREA_FK100': '', 'CTX_AREA_NK100': ''}
        rows = []
        for page in range(20):
            response = await self.request('GET', 'inquire-balance', self.tr('8434R'), params, tr_cont='N' if page else '')
            body = response['body']
            rows.extend(body.get('output1') or [])
            if response['headers'].get('tr_cont', '') not in {'M', 'F'}:
                summary = body.get('output2') or []
                return {'provider': 'kis', 'mock': self.mode in {'mock','mock2'}, 'positions': rows,
                        'summary': summary[0] if isinstance(summary, list) and summary else summary}
            params['CTX_AREA_FK100'] = body.get('ctx_area_fk100', '')
            params['CTX_AREA_NK100'] = body.get('ctx_area_nk100', '')
        raise RuntimeError('KIS balance pagination incomplete')

    async def buying_power(self, symbol: str, price: Decimal, *, market: bool = False) -> dict[str, Any]:
        params = self.account_params() | {'PDNO': symbol, 'ORD_UNPR': '0' if market else str(price), 'ORD_DVSN': '01' if market else '00',
            'CMA_EVLU_AMT_ICLD_YN': 'N', 'OVRS_ICLD_YN': 'N'}
        return (await self.request('GET', 'inquire-psbl-order', self.tr('8908R'), params))['body']['output']

    async def submit_order(self, intent: OrderIntent) -> dict:
        if getattr(self, '_common_owner_bound', False):
            raise RuntimeError('Selected KIS route requires the common owner submit permit')
        return await self._submit_order(intent)

    async def submit_order_with_permit(self, intent: OrderIntent, permit) -> dict:
        if not callable(permit):
            raise TypeError('KIS guarded submit requires a dispatch permit')
        return await self._submit_order(intent, permit=permit)

    async def _submit_order(self, intent: OrderIntent, *, permit=None) -> dict:
        if intent.instrument.asset_class not in {AssetClass.ETF, AssetClass.EQUITY}:
            raise ValueError('KIS cash endpoint supports domestic stocks/ETFs only')
        symbol = intent.instrument.raw_symbol
        quantity = intent.quantity
        if len(symbol) != 6 or not symbol.isdigit() or intent.side not in {Side.BUY, Side.SELL}:
            raise ValueError('Invalid KIS domestic cash symbol/side')
        if quantity is None or not quantity.is_finite() or quantity <= 0 or quantity != quantity.to_integral_value():
            raise ValueError('KIS cash quantity must be a positive whole number of shares')
        price = intent.limit_price
        if price is not None and (not price.is_finite() or price <= 0 or price != price.to_integral_value()):
            raise ValueError('KIS cash limit price must be a positive integral KRW amount')
        body = self.account_params() | {'PDNO': symbol, 'ORD_DVSN': '00' if price is not None else '01',
            'ORD_QTY': str(int(quantity)), 'ORD_UNPR': str(int(price)) if price is not None else '0',
            'EXCG_ID_DVSN_CD': 'KRX', 'SLL_TYPE': '01' if intent.side == Side.SELL else ''}
        kwargs = {'permit':permit} if permit is not None else {}
        result = await self.request('POST', 'order-cash', self.tr('0012U' if intent.side == Side.BUY else '0011U'), body, **kwargs)
        output = result['body'].get('output') or {}
        if not output.get('ODNO') and not output.get('odno'):
            raise RuntimeError('KIS accepted response missing order number; reconciliation required')
        return output

    async def order_status(self, order_number: str, *, day: str | None = None, symbol: str = '') -> dict:
        day = day or datetime.now(KST).strftime('%Y%m%d')
        params = self.account_params() | {'INQR_STRT_DT': day, 'INQR_END_DT': day, 'SLL_BUY_DVSN_CD': '00',
            'PDNO': symbol, 'CCLD_DVSN': '00', 'INQR_DVSN': '00', 'INQR_DVSN_3': '00',
            'ORD_GNO_BRNO': '', 'ODNO': order_number, 'INQR_DVSN_1': '',
            'CTX_AREA_FK100': '', 'CTX_AREA_NK100': '', 'EXCG_ID_DVSN_CD': 'KRX'}
        for page in range(20):
            response = await self.request('GET', 'inquire-daily-ccld', self.tr('0081R'), params, tr_cont='N' if page else '')
            body = response['body']
            for row in body.get('output1') or []:
                if str(row.get('odno', '')).lstrip('0') == str(order_number).lstrip('0'):
                    return row
            if response['headers'].get('tr_cont', '') not in {'M', 'F'}:
                break
            params['CTX_AREA_FK100'] = body.get('ctx_area_fk100', '')
            params['CTX_AREA_NK100'] = body.get('ctx_area_nk100', '')
        raise RuntimeError('KIS order has not appeared in the broker ledger; retain pending state')

    async def daily_fills(self, day: str) -> list[dict]:
        """Read the complete day across pages, including account-side manual fills."""
        params = self.account_params() | {'INQR_STRT_DT': day.replace('-', ''), 'INQR_END_DT': day.replace('-', ''),
            'SLL_BUY_DVSN_CD': '00', 'PDNO': '', 'CCLD_DVSN': '01', 'INQR_DVSN': '00', 'INQR_DVSN_3': '00',
            'ORD_GNO_BRNO': '', 'ODNO': '', 'INQR_DVSN_1': '', 'CTX_AREA_FK100': '', 'CTX_AREA_NK100': '',
            'EXCG_ID_DVSN_CD': 'KRX'}
        rows = []
        for page in range(20):
            response = await self.request('GET', 'inquire-daily-ccld', self.tr('0081R'), params, tr_cont='N' if page else '')
            for row in response['body'].get('output1') or []:
                qty = int(Decimal(str(row.get('tot_ccld_qty') or 0)))
                if qty <= 0:
                    continue
                side = {'01':'sell', '02':'buy'}.get(str(row.get('sll_buy_dvsn_cd', '')))
                if side is None or not row.get('odno') or not row.get('pdno'):
                    raise RuntimeError('Incomplete broker fill identity/side')
                rows.append({'day':day,'order_number':row['odno'],'symbol':row['pdno'],'side':side,
                    'quantity':qty,'notional':str(row['tot_ccld_amt'])})
            if response['headers'].get('tr_cont', '') not in {'M', 'F'}:
                return rows
            params['CTX_AREA_FK100'] = response['body'].get('ctx_area_fk100', '')
            params['CTX_AREA_NK100'] = response['body'].get('ctx_area_nk100', '')
        raise RuntimeError('KIS daily fill pagination incomplete')

    async def cancel_order(self, order_number: str, organization: str) -> dict:
        if getattr(self, '_common_owner_bound', False):
            raise RuntimeError('Selected KIS route requires the common owner cancel permit')
        body = self.account_params() | {'KRX_FWDG_ORD_ORGNO': organization, 'ORGN_ODNO': order_number,
            'ORD_DVSN': '00', 'RVSE_CNCL_DVSN_CD': '02', 'ORD_QTY': '0', 'ORD_UNPR': '0',
            'QTY_ALL_ORD_YN': 'Y', 'EXCG_ID_DVSN_CD': 'KRX'}
        return (await self.request('POST', 'order-rvsecncl', self.tr('0013U'), body))['body']

    async def cancel_order_with_permit(self, order_number: str, organization: str, permit) -> dict:
        """Cancel a durable KIS order only through the common one-use permit."""
        if not callable(permit):
            raise TypeError('KIS guarded cancel requires a dispatch permit')
        body = self.account_params() | {'KRX_FWDG_ORD_ORGNO': organization, 'ORGN_ODNO': order_number,
            'ORD_DVSN': '00', 'RVSE_CNCL_DVSN_CD': '02', 'ORD_QTY': '0', 'ORD_UNPR': '0',
            'QTY_ALL_ORD_YN': 'Y', 'EXCG_ID_DVSN_CD': 'KRX'}
        return (await self.request('POST', 'order-rvsecncl', self.tr('0013U'), body,
                                   permit=permit))['body']
