# TX00/TOSS independent production-source audit — 400 words maximum
READONLYroleaudit, qualifiedopencode/deepseek-v4.1-flash-free (registered auditprofile). ProductionguardswerewrittenbyFreeLLMAPI GLM4.5 andOpenCodeMiMo, differentmodelfamilies; DeepSeekchangedonlyoldtestexpectation, notproductionguards. Codexalsohardenedonefinaldispatchlineagainststatefulstr.upperoverride. ReviewACTUALSOURCEINLINEbelow, nofiles/tools/exploration/edits/tests/agentsneeded. Returnconcreteproblems(function/line,repro)orNO_CRITICAL_FINDINGS, <=400words. Noacceptanceclaims.
ContractTOSS=DATA_ONLY: submit_orderrejectsBEFOREintent/client/accountcallbacks; BOTHgenericrequestanddirect_request_once rejectallnonGET/nonstringBEFOREauth/session. AnyacceptedtransportusesONLYGET, cannotreinterpretvalidatedmethodintoPOST. OAuth POST /oauth2/token explicitlypermittedforread-onlycollectoraccess; OAuth/cache/otherbrokersunchangedasAST-normalizedproof. GET401refreshatmostonceunchanged. No newbypassflag, liveaccount, externalordersordeploy. Parent5VenuecapabilityTX00remainsincomplete. 43independentpytestcasesPASSincludingdirectclient,invalidmethod,bombcallbacks,GET401casesandstatefulstrmethodcounterexample. Existing _safe_json isunchangedJSONresponseparser; not authorizationboundary. Trustedcallerconfiguredrest_url; wearenotclaimingahostprocesssecuritysandbox. FocusfiniteDATA_ONLYboundariesandpreservationonly.

BEGIN EXACT SOURCE
FILE vibetrading/brokers.py sha256=345ea4ae6c8d1414fee1aba4913f1e8062ad3e8c605f2fbb53af823be99a3746

285:     async def submit_order(self, intent: OrderIntent) -> dict:
286:         raise RuntimeError("TOSS DATA_ONLY: submit_order rejected - mutations not permitted")

FILE vibetrading/providers/toss.py sha256=37db32eea5cac44b0fc79287c6a8eed74c2dd9894f84470f6f09e5f60d33d855

202:     async def _access_token_locked(self) -> str:
203:         cached = self._read_token_cache()
204:         if cached:
205:             return cached
206:         if not self.client_id or not self.client_secret:
207:             raise RuntimeError("TOSS_API_KEY/TOSS_API_SECRET are not configured")
208:         timeout = aiohttp.ClientTimeout(total=20)
209:         async with aiohttp.ClientSession(timeout=timeout) as session:
210:             async with session.post(
211:                 f"{self.rest_url}/oauth2/token",
212:                 data={
213:                     "grant_type": "client_credentials",
214:                     "client_id": self.client_id,
215:                     "client_secret": self.client_secret,
216:                 },
217:                 headers={"Content-Type": "application/x-www-form-urlencoded"},
218:             ) as response:
219:                 data = await _safe_json(response)
220:                 if response.status >= 400:
221:                     raise RuntimeError(
222:                         f"Toss token failed status={response.status} error={data.get('error')} "
223:                         f"description={data.get('error_description')}"
224:                     )
225:                 token = data.get("access_token")
226:                 if not token:
227:                     raise RuntimeError("Toss token response missing access_token")
228:                 expires_in = int(data.get("expires_in") or 86_400)
229:                 self._write_token_cache(str(token), int(time.time()) + max(60, expires_in - 300))
230:                 return str(token)

232:     async def request(
233:         self,
234:         method: str,
235:         path: str,
236:         *,
237:         params: dict[str, Any] | None = None,
238:         json_body: dict[str, Any] | None = None,
239:         account_seq: str | None = None,
240:     ) -> dict[str, Any]:
241:         # Early GET-only mutation gate for TOSS DATA_ONLY policy.  A
242:         # non-string method cannot be proven to be a read, so it is rejected
243:         # here before authentication or any transport access.
244:         normalized_method = method.upper() if isinstance(method, str) else None
245:         if normalized_method != "GET":
246:             raise RuntimeError("TOSS DATA_ONLY: request rejected - mutations not permitted")
247:         token = await self.access_token()
248:         for attempt in range(2):
249:             try:
250:                 return await self._request_once(method, path, token=token, params=params,
251:                                                 json_body=json_body, account_seq=account_seq)
252:             except TossUnauthorized:
253:                 # Read requests can safely be repeated once. Do not replay
254:                 # order POSTs or mask a rejected fresh credential.
255:                 if attempt or method.upper() != "GET":
256:                     raise
257:                 token = await self.refresh_rejected_token(token)
258:         raise AssertionError("unreachable")

260:     async def _request_once(self, method: str, path: str, *, token: str,
261:                             params: dict[str, Any] | None, json_body: dict[str, Any] | None,
262:                             account_seq: str | None) -> dict[str, Any]:
263:         # Early GET-only mutation gate for TOSS DATA_ONLY policy.  Runs
264:         # before headers or a ClientSession exist so a blocked method can
265:         # never touch the wire or leak a token.
266:         normalized_method = method.upper() if isinstance(method, str) else None
267:         if normalized_method != "GET":
268:             raise RuntimeError("TOSS DATA_ONLY: _request_once rejected - mutations not permitted")
269:         headers = {"Authorization": f"Bearer {token}"}
270:         if account_seq is not None:
271:             headers["X-Tossinvest-Account"] = str(account_seq)
272:         timeout = aiohttp.ClientTimeout(total=20)
273:         async with aiohttp.ClientSession(timeout=timeout) as session:
274:             request = session.get  # The validated DATA_ONLY transport is always a read.
275:             async with request(f"{self.rest_url}{path}", params=params, json=json_body, headers=headers) as response:
276:                 data = await _safe_json(response)
277:                 if response.status >= 400 or data.get("error"):
278:                     error = data.get("error") if isinstance(data.get("error"), dict) else data
279:                     error_type = TossUnauthorized if response.status == 401 else RuntimeError
280:                     raise error_type(
281:                         f"Toss {path} failed status={response.status} "
282:                         f"code={error.get('code') or error.get('error')} "
283:                         f"msg={error.get('message') or error.get('error_description')}"
284:                     )
285:                 return data
END SOURCE
