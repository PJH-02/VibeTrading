> ## Documentation Index
> Fetch the complete documentation index at: https://docs.arcus.xyz/llms.txt
> Use this file to discover all available pages before exploring further.

# Markets

> > **Endpoint:** `wss://api.arcus.xyz/v1/ws` — one socket multiplexes every channel and RPC request. Click **Connect**, then send the example subscribe message below to start receiving data.

Global channel — no `id` required. Streams full per-market metadata for all listed markets.

Snapshot-only channel: every message is a full map of all markets keyed by `marketId` (same shape on subscribe and on every periodic re-emission, no partial diffs). Clients replace their entire markets view on each message.

**Cadence:** ~5s periodic snapshot, plus an immediate snapshot when a new funding rate is applied (~hourly) so the just-applied `fundingRate` propagates without waiting for the next periodic snapshot.

**Field coverage today:** `marketDisplayName`, `marketId`, `baseAsset`, `quoteAsset`, `status` (currently always `ONLINE`), static config (tick/step/min/max sizes, margin fractions, fees), `oraclePrice`, `fundingRate`, `nextFundingRate`, and `nextFundingAt` are populated. `priceChange24h` is the fractional change in the **mark price** over the trailing 24h (not the oracle price). `volume24h`, `high24h`, `low24h`, and `trades24h` are rolling 24h aggregates, refreshed periodically. High/low use only closed candles with at least one trade (gap-fill buckets are excluded). `openInterest` is the total size of all open long positions per market (equals total short), in base asset units, refreshed from a periodic snapshot (default ~60s) so it may lag real time. `openInterestCapNotional` (`null` when no cap is active) is the operator-set maximum open-interest USD notional, refreshed on the same cadence.




## AsyncAPI

````yaml api-reference/asyncapi.yaml markets
id: markets
title: Markets
description: >
  > **Endpoint:** `wss://api.arcus.xyz/v1/ws` — one socket multiplexes every
  channel and RPC request. Click **Connect**, then send the example subscribe
  message below to start receiving data.


  Global channel — no `id` required. Streams full per-market metadata for all
  listed markets.


  Snapshot-only channel: every message is a full map of all markets keyed by
  `marketId` (same shape on subscribe and on every periodic re-emission, no
  partial diffs). Clients replace their entire markets view on each message.


  **Cadence:** ~5s periodic snapshot, plus an immediate snapshot when a new
  funding rate is applied (~hourly) so the just-applied `fundingRate` propagates
  without waiting for the next periodic snapshot.


  **Field coverage today:** `marketDisplayName`, `marketId`, `baseAsset`,
  `quoteAsset`, `status` (currently always `ONLINE`), static config
  (tick/step/min/max sizes, margin fractions, fees), `oraclePrice`,
  `fundingRate`, `nextFundingRate`, and `nextFundingAt` are populated.
  `priceChange24h` is the fractional change in the **mark price** over the
  trailing 24h (not the oracle price). `volume24h`, `high24h`, `low24h`, and
  `trades24h` are rolling 24h aggregates, refreshed periodically. High/low use
  only closed candles with at least one trade (gap-fill buckets are excluded).
  `openInterest` is the total size of all open long positions per market (equals
  total short), in base asset units, refreshed from a periodic snapshot (default
  ~60s) so it may lag real time. `openInterestCapNotional` (`null` when no cap
  is active) is the operator-set maximum open-interest USD notional, refreshed
  on the same cadence.
servers:
  - id: mainnet
    protocol: wss
    host: api.arcus.xyz
    bindings: []
    variables: []
  - id: testnet
    protocol: wss
    host: api.testnet.arcus.xyz
    bindings: []
    variables: []
address: /v1/ws?channel=markets
parameters: []
bindings: []
operations:
  - &ref_3
    id: subscribeMarkets
    title: Subscribe markets
    type: receive
    messages:
      - &ref_5
        id: subscribe
        contentType: application/json
        payload:
          - name: Subscribe to the markets channel
            type: object
            properties:
              - name: type
                type: string
                description: subscribe
                required: true
              - name: channel
                type: string
                enumValues:
                  - markets
                  - exchangeAttributeUpdates
                required: true
        headers: []
        jsonPayloadSchema:
          type: object
          required:
            - type
            - channel
          properties:
            type:
              type: string
              const: subscribe
              x-parser-schema-id: <anonymous-schema-59>
            channel:
              type: string
              enum:
                - markets
                - exchangeAttributeUpdates
              x-parser-schema-id: <anonymous-schema-60>
          x-parser-schema-id: SubscribeGlobalPayload
        title: Subscribe to the markets channel
        example: |-
          {
            "type": "subscribe",
            "channel": "markets"
          }
        bindings: []
        extensions:
          - id: x-parser-unique-object-id
            value: subscribe
    bindings: []
    extensions: &ref_2
      - id: x-parser-unique-object-id
        value: markets
  - &ref_4
    id: receiveMarkets
    title: Receive markets
    type: send
    messages:
      - &ref_6
        id: snapshot
        contentType: application/json
        payload:
          - allOf: &ref_0
              - type: object
                description: Common envelope for all server-pushed channel messages.
                required:
                  - type
                  - channel
                properties:
                  type:
                    type: string
                    enum:
                      - subscribed
                      - channel_data
                    description: >-
                      `subscribed` for the initial snapshot; `channel_data` for
                      updates.
                    x-parser-schema-id: <anonymous-schema-7>
                  channel:
                    type: string
                    description: Channel name.
                    x-parser-schema-id: <anonymous-schema-8>
                  id:
                    type: string
                    description: >
                      Channel id (market, or the lowercase master Ethereum
                      address on account-scoped channels). Absent for global
                      channels. Always lowercase for addresses regardless of the
                      spelling used to subscribe, so compare it
                      case-insensitively.
                    x-parser-schema-id: <anonymous-schema-9>
                  accountIndex:
                    type: integer
                    description: >
                      Subaccount this frame belongs to. Present on EVERY frame
                      of an account-scoped channel — `subscribed`,
                      `unsubscribed`, `degraded`, and `channel_data` alike —
                      including when it is 0, so a frame names its subaccount
                      outright and no client needs a "missing means 0" rule.
                      Omitted on every other channel. This is what makes several
                      subaccounts of one address demuxable on a single
                      connection: `id` alone is the master address and is
                      identical across all of them.
                    minimum: 0
                    maximum: 9
                    example: 3
                    x-parser-schema-id: <anonymous-schema-10>
                  sigFigs:
                    type: integer
                    description: >
                      Echo of the l2Orderbook aggregation this frame belongs to.
                      Omitted on full-precision views and on every other
                      channel. Lets a client holding several aggregations of the
                      same market demux snapshots.
                    enum:
                      - 2
                      - 3
                      - 4
                      - 5
                    x-parser-schema-id: <anonymous-schema-11>
                  roundStep:
                    type: integer
                    description: >-
                      Echo of the l2Orderbook roundStep for this view. Omitted
                      unless sigFigs is 5 and roundStep was 2 or 5.
                    enum:
                      - 1
                      - 2
                      - 5
                    x-parser-schema-id: <anonymous-schema-12>
                  market:
                    type: string
                    example: BTC-USD
                    description: >
                      Display ticker of the market this frame belongs to, always
                      the resolved ticker even when the client subscribed by
                      numeric id. Present only on `positions` / `orders` /
                      `userFills`; omitted on every other channel. Its meaning
                      differs slightly by frame type. On `subscribed` /
                      `unsubscribed` it echoes the market filter the
                      subscription was created with, and is omitted for an
                      unfiltered subscription. On `channel_data` it is the
                      market of the frame itself, so an UNFILTERED subscriber
                      sees it too — those channels emit one frame per market, so
                      every frame has one. This is what lets a client demux,
                      exactly as `sigFigs` / `roundStep` do for l2Orderbook: a
                      connection holding several market-filtered views of one
                      account matches each frame to the right view by this
                      field. Clients that hold a single unfiltered view can
                      ignore it.
                    x-parser-schema-id: <anonymous-schema-13>
                  contents:
                    type: object
                    additionalProperties: true
                    description: Channel-specific payload. Schema varies by channel.
                    properties: {}
                    x-parser-schema-id: <anonymous-schema-14>
                x-parser-schema-id: ChannelDataEnvelope
              - type: object
                properties:
                  contents:
                    type: object
                    required:
                      - isSnapshot
                      - markets
                    properties:
                      isSnapshot:
                        type: boolean
                        description: >
                          Always `true` on this channel — every emission is a
                          full snapshot, not a partial diff.
                        example: true
                        x-parser-schema-id: <anonymous-schema-63>
                      markets:
                        type: object
                        description: >
                          All markets keyed by `marketId` as a string. Same
                          per-market shape as REST `GET /v1/markets`
                          (`MarketInfo`).
                        additionalProperties:
                          type: object
                          description: One market entry. Matches REST `MarketInfo`.
                          properties:
                            marketDisplayName:
                              type: string
                              example: BTC-USD
                              x-parser-schema-id: <anonymous-schema-66>
                            marketId:
                              type: integer
                              example: 1
                              x-parser-schema-id: <anonymous-schema-67>
                            status:
                              type: string
                              example: ONLINE
                              x-parser-schema-id: <anonymous-schema-68>
                            baseAsset:
                              type: string
                              example: BTC
                              x-parser-schema-id: <anonymous-schema-69>
                            quoteAsset:
                              type: string
                              example: USD
                              x-parser-schema-id: <anonymous-schema-70>
                            tickSize:
                              type: string
                              x-parser-schema-id: <anonymous-schema-71>
                            stepSize:
                              type: string
                              x-parser-schema-id: <anonymous-schema-72>
                            minOrderSize:
                              type: string
                              description: Minimum order quantity, in base-asset units.
                              example: '0.0001'
                              x-parser-schema-id: <anonymous-schema-73>
                            maxOrderSize:
                              type: string
                              description: >
                                Maximum order quantity, in base-asset units. A
                                per-order security gate rather than a dust
                                filter — reduce-only orders are NOT exempt.
                              example: '10000'
                              x-parser-schema-id: <anonymous-schema-74>
                            oraclePrice:
                              type: string
                              example: '94500.00'
                              x-parser-schema-id: <anonymous-schema-75>
                            markPrice:
                              type: string
                              description: >
                                Authoritative mark price (decimal string) — the
                                sole risk-valuation price. "0" means
                                unavailable; callers must not fall back to
                                `oraclePrice`.
                              example: '94500.50'
                              x-parser-schema-id: <anonymous-schema-76>
                            lastTradePrice:
                              type: string
                              description: >-
                                Price of the most recent trade (decimal string).
                                Omitted until the market has traded.
                              example: '94498.00'
                              x-parser-schema-id: <anonymous-schema-77>
                            fundingRate:
                              type: string
                              description: Most recently applied funding rate.
                              example: '0.0001'
                              x-parser-schema-id: <anonymous-schema-78>
                            nextFundingRate:
                              type: string
                              description: >-
                                In-progress forecast for the next funding
                                payment.
                              example: '0.00012'
                              x-parser-schema-id: <anonymous-schema-79>
                            nextFundingAt:
                              type: integer
                              format: int64
                              description: Unix seconds for the next funding payment time.
                              x-parser-schema-id: <anonymous-schema-80>
                            priceChange24h:
                              type: string
                              description: >-
                                Fractional change in the mark price (not the
                                oracle price) over the trailing 24h (e.g.
                                "0.025" = +2.5%), computed from a rolling
                                per-market ring of the first mark price seen
                                each minute.
                              x-parser-schema-id: <anonymous-schema-81>
                            volume24h:
                              type: string
                              description: >-
                                Base-asset trade volume over the trailing 24h,
                                from the rolling-24h aggregator.
                              x-parser-schema-id: <anonymous-schema-82>
                            high24h:
                              type: string
                              description: >-
                                Highest transaction price over the trailing 24h,
                                from closed 1m candles with at least one trade.
                              x-parser-schema-id: <anonymous-schema-83>
                            low24h:
                              type: string
                              description: >-
                                Lowest transaction price over the trailing 24h,
                                from closed 1m candles with at least one trade.
                              x-parser-schema-id: <anonymous-schema-84>
                            trades24h:
                              type: integer
                              format: int64
                              description: >-
                                Trade count over the trailing 24h, from the
                                rolling-24h aggregator.
                              x-parser-schema-id: <anonymous-schema-85>
                            openInterest:
                              type: string
                              description: >-
                                Total size of all open long positions in the
                                market (equals total short), in base asset
                                units. Refreshed from a periodic snapshot, so it
                                may lag real time.
                              x-parser-schema-id: <anonymous-schema-86>
                            openInterestCapNotional:
                              type: string
                              description: >-
                                Operator-set maximum open interest as a
                                USD-notional decimal string (openInterest × mark
                                price). At the cap the engine rejects
                                OI-increasing fills with rejection reason
                                OPEN_INTEREST_CAP_EXCEEDED; OI-neutral and
                                OI-reducing trades still match. `null` when no
                                cap is active. Refreshed from a periodic
                                snapshot, so it may lag real time.
                              example: '50000000'
                              nullable: true
                              x-parser-schema-id: <anonymous-schema-87>
                            initialMarginFraction:
                              type: string
                              x-parser-schema-id: <anonymous-schema-88>
                            maintenanceMarginFraction:
                              type: string
                              x-parser-schema-id: <anonymous-schema-89>
                            offHoursInitialMarginFraction:
                              type: string
                              description: >-
                                Initial margin fraction the engine enforces
                                outside `regularTradingHours`
                                (overnight/weekends for equity-class markets);
                                equals `initialMarginFraction` for 24/7 markets.
                                Maintenance margin is not uplifted off-hours.
                              x-parser-schema-id: <anonymous-schema-90>
                            currentSettlementPrice:
                              type: string
                              description: >-
                                Sealed VWAP settlement price (decimal USD)
                                anchoring the off-hours trading band. Present
                                only while outside RTH with a sealed anchor;
                                omitted in-RTH and for 24/7 markets.
                              x-parser-schema-id: <anonymous-schema-91>
                            upperTradingBound:
                              type: string
                              description: >-
                                Actual upper price boundary (decimal USD) of the
                                off-hours trading band the engine enforces
                                (anchor + anchor × initialMarginFraction ×
                                bandMultiplier; the multiplier walks the
                                0.5×→1×→2×→4× ladder independently per side).
                                Fills at or above it are halted. Present only
                                while outside RTH with a sealed anchor; omitted
                                in-RTH and for 24/7 markets.
                              x-parser-schema-id: <anonymous-schema-92>
                            lowerTradingBound:
                              type: string
                              description: >-
                                Actual lower price boundary (decimal USD) of the
                                off-hours trading band. Fills at or below it are
                                halted. Present only while outside RTH with a
                                sealed anchor; omitted in-RTH and for 24/7
                                markets.
                              x-parser-schema-id: <anonymous-schema-93>
                            nextUpperTradingBound:
                              type: string
                              description: >-
                                Price (decimal USD) the upper boundary moves to
                                if the upper band expands one level; equals
                                upperTradingBound at the 4× ceiling. Present
                                only while outside RTH with a sealed anchor.
                              x-parser-schema-id: <anonymous-schema-94>
                            nextLowerTradingBound:
                              type: string
                              description: >-
                                Price (decimal USD) the lower boundary moves to
                                if the lower band expands one level; equals
                                lowerTradingBound at the 4× ceiling. Present
                                only while outside RTH with a sealed anchor.
                              x-parser-schema-id: <anonymous-schema-95>
                            isUpperInExpansionZone:
                              type: boolean
                              description: >-
                                Whether the upper band side is in its expansion
                                zone — the 1-hour EWMA of the bid-side impact
                                price is holding within 90% of the upper
                                boundary; holding for one continuous hour
                                expands the band one level. The zones are
                                independent per side. Present only while outside
                                RTH with a sealed anchor.
                              x-parser-schema-id: <anonymous-schema-96>
                            isLowerInExpansionZone:
                              type: boolean
                              description: >-
                                Whether the lower band side is in its expansion
                                zone. Present only while outside RTH with a
                                sealed anchor.
                              x-parser-schema-id: <anonymous-schema-97>
                            upperZoneEnteredAt:
                              type: integer
                              format: int64
                              description: >-
                                Unix seconds at which the upper side entered its
                                expansion zone. Present only while
                                isUpperInExpansionZone is true.
                              x-parser-schema-id: <anonymous-schema-98>
                            upperExpectedExpansionAt:
                              type: integer
                              format: int64
                              description: >-
                                Unix seconds of the earliest time the upper
                                bound can expand — exactly one hour after
                                upperZoneEnteredAt. Not a guarantee; the EWMA
                                must still be in the zone when the hour
                                completes. Present only while
                                isUpperInExpansionZone is true.
                              x-parser-schema-id: <anonymous-schema-99>
                            lowerZoneEnteredAt:
                              type: integer
                              format: int64
                              description: >-
                                Unix seconds at which the lower side entered its
                                expansion zone. Present only while
                                isLowerInExpansionZone is true.
                              x-parser-schema-id: <anonymous-schema-100>
                            lowerExpectedExpansionAt:
                              type: integer
                              format: int64
                              description: >-
                                Unix seconds of the earliest time the lower
                                bound can expand. Present only while
                                isLowerInExpansionZone is true.
                              x-parser-schema-id: <anonymous-schema-101>
                            regularTradingHours:
                              type: object
                              description: >-
                                Session window during which the base margin
                                fractions apply; outside it the off-hours
                                fractions apply. Omitted for 24/7 markets
                                (crypto).
                              properties:
                                startSecondsOfDay:
                                  type: integer
                                  format: int32
                                  x-parser-schema-id: <anonymous-schema-103>
                                endSecondsOfDay:
                                  type: integer
                                  format: int32
                                  x-parser-schema-id: <anonymous-schema-104>
                                timezone:
                                  type: string
                                  example: America/New_York
                                  x-parser-schema-id: <anonymous-schema-105>
                                isOvernight:
                                  type: boolean
                                  x-parser-schema-id: <anonymous-schema-106>
                              x-parser-schema-id: <anonymous-schema-102>
                            type:
                              type: string
                              example: PERPETUAL
                              x-parser-schema-id: <anonymous-schema-107>
                            assetResolution:
                              type: string
                              x-parser-schema-id: <anonymous-schema-108>
                            pythId:
                              type: string
                              x-parser-schema-id: <anonymous-schema-109>
                          x-parser-schema-id: <anonymous-schema-65>
                        properties: {}
                        x-parser-schema-id: <anonymous-schema-64>
                    x-parser-schema-id: <anonymous-schema-62>
                x-parser-schema-id: <anonymous-schema-61>
            x-parser-schema-id: MarketsUpdatePayload
            name: Markets Snapshot
            description: >
              Full markets state delivered as the subscribe response. Same shape
              as the periodic `MarketsUpdate` — the markets channel is
              snapshot-only.
        headers: []
        jsonPayloadSchema: &ref_1
          allOf: *ref_0
          x-parser-schema-id: MarketsUpdatePayload
        title: Markets Snapshot
        description: >
          Full markets state delivered as the subscribe response. Same shape as
          the periodic `MarketsUpdate` — the markets channel is snapshot-only.
        example: '{}'
        bindings: []
        extensions:
          - id: x-parser-unique-object-id
            value: snapshot
      - &ref_7
        id: update
        contentType: application/json
        payload:
          - allOf: *ref_0
            x-parser-schema-id: MarketsUpdatePayload
            name: Markets Update
            description: >
              Periodic re-snapshot of all markets. Replaces the client's full
              markets view; there are no partial diffs on this channel.
        headers: []
        jsonPayloadSchema: *ref_1
        title: Markets Update
        description: >
          Periodic re-snapshot of all markets. Replaces the client's full
          markets view; there are no partial diffs on this channel.
        example: |-
          {
            "type": "channel_data",
            "channel": "markets",
            "contents": {
              "isSnapshot": true,
              "markets": {
                "1": {
                  "marketDisplayName": "BTC-USD",
                  "marketId": 1,
                  "status": "ONLINE",
                  "baseAsset": "BTC",
                  "quoteAsset": "USD",
                  "tickSize": "0.01",
                  "stepSize": "0.0001",
                  "minOrderSize": "0.0001",
                  "maxOrderSize": "10000",
                  "oraclePrice": "94500.00",
                  "markPrice": "94500.50",
                  "lastTradePrice": "94498.00",
                  "fundingRate": "0.0001",
                  "nextFundingRate": "0.00012",
                  "nextFundingAt": 1712349600,
                  "priceChange24h": "0.025",
                  "volume24h": "1234.5678",
                  "trades24h": 4242,
                  "openInterest": "312.4567",
                  "initialMarginFraction": "0.05",
                  "maintenanceMarginFraction": "0.03",
                  "type": "PERPETUAL"
                },
                "2": {
                  "marketDisplayName": "ETH-USD",
                  "marketId": 2,
                  "status": "ONLINE",
                  "baseAsset": "ETH",
                  "quoteAsset": "USD",
                  "oraclePrice": "3050.00",
                  "markPrice": "3050.25",
                  "lastTradePrice": "3049.80",
                  "fundingRate": "0.00005",
                  "nextFundingRate": "0.00007",
                  "nextFundingAt": 1712349600,
                  "type": "PERPETUAL"
                }
              }
            }
          }
        bindings: []
        extensions:
          - id: x-parser-unique-object-id
            value: update
    bindings: []
    extensions: *ref_2
sendOperations:
  - *ref_3
receiveOperations:
  - *ref_4
sendMessages:
  - *ref_5
receiveMessages:
  - *ref_6
  - *ref_7
extensions:
  - id: x-parser-unique-object-id
    value: markets
securitySchemes: []

````

This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.