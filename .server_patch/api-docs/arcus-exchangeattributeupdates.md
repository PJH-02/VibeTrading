> ## Documentation Index
> Fetch the complete documentation index at: https://docs.arcus.xyz/llms.txt
> Use this file to discover all available pages before exploring further.

# Exchange attribute updates

> > **Endpoint:** `wss://api.arcus.xyz/v1/ws` — one socket multiplexes every channel and RPC request. Click **Connect**, then send the example subscribe message below to start receiving data.

Exchange-wide attribute state, discriminated by entry `type`:

`"feeTierConfig"` carries the full fee tier table whenever the fee tier table is updated.

`"marketAttributes"` (DEPRECATED — superseded by the dedicated `marketAttributes` channel, whose entries carry the market's full attribute state; this variant keeps streaming for existing consumers and will be removed after a deprecation window) carries per-market Regular Trading Hours and off-hours trading-bound events: RTH boundary crossings (`isOutsideRth`, with `boundEvent`: `exitRth` / `enterRth`), VWAP settlement seals (`sealedVwapSettlementPrice`), and off-hours expansion-zone events (`boundEvent`: `expansionZoneEntered` when a side's impact-price EWMA crosses within 90% of its trading boundary, starting the 1-hour expansion clock; `expansionZoneExited` when the EWMA relaxes out of the zone and the clock resets; `tradingBoundExpanded` when the clock completes and that side's band widens one level on the 0.5×→1×→2×→4× ladder).

**Global channel — no `id` required.** All subscribers receive the same stream.

On subscribe: snapshot of the current fee tier table (one `feeTierConfig` entry, `isSnapshot: true`). Streaming: fee tier table replacements and marketAttributes edge events (`isSnapshot: false`).

The equivalent REST reads are `GET /v1/feetiers` and `GET /v1/markets` (`isOutsideRth`, `isUpperInExpansionZone` / `isLowerInExpansionZone`, `upperTradingBound` / `lowerTradingBound`).




## AsyncAPI

````yaml api-reference/asyncapi.yaml exchangeAttributeUpdates
id: exchangeAttributeUpdates
title: Exchange attribute updates
description: >
  > **Endpoint:** `wss://api.arcus.xyz/v1/ws` — one socket multiplexes every
  channel and RPC request. Click **Connect**, then send the example subscribe
  message below to start receiving data.


  Exchange-wide attribute state, discriminated by entry `type`:


  `"feeTierConfig"` carries the full fee tier table whenever the fee tier table
  is updated.


  `"marketAttributes"` (DEPRECATED — superseded by the dedicated
  `marketAttributes` channel, whose entries carry the market's full attribute
  state; this variant keeps streaming for existing consumers and will be removed
  after a deprecation window) carries per-market Regular Trading Hours and
  off-hours trading-bound events: RTH boundary crossings (`isOutsideRth`, with
  `boundEvent`: `exitRth` / `enterRth`), VWAP settlement seals
  (`sealedVwapSettlementPrice`), and off-hours expansion-zone events
  (`boundEvent`: `expansionZoneEntered` when a side's impact-price EWMA crosses
  within 90% of its trading boundary, starting the 1-hour expansion clock;
  `expansionZoneExited` when the EWMA relaxes out of the zone and the clock
  resets; `tradingBoundExpanded` when the clock completes and that side's band
  widens one level on the 0.5×→1×→2×→4× ladder).


  **Global channel — no `id` required.** All subscribers receive the same
  stream.


  On subscribe: snapshot of the current fee tier table (one `feeTierConfig`
  entry, `isSnapshot: true`). Streaming: fee tier table replacements and
  marketAttributes edge events (`isSnapshot: false`).


  The equivalent REST reads are `GET /v1/feetiers` and `GET /v1/markets`
  (`isOutsideRth`, `isUpperInExpansionZone` / `isLowerInExpansionZone`,
  `upperTradingBound` / `lowerTradingBound`).
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
address: /v1/ws?channel=exchangeAttributeUpdates
parameters: []
bindings: []
operations:
  - &ref_3
    id: subscribeExchangeAttributes
    title: Subscribe exchange attributes
    type: receive
    messages:
      - &ref_5
        id: subscribe
        contentType: application/json
        payload:
          - name: Subscribe to the exchange attribute updates channel
            type: object
            properties:
              - name: type
                type: string
                description: subscribe
                required: true
              - name: channel
                type: string
                description: exchangeAttributeUpdates
                required: true
              - name: snapshot
                type: boolean
                description: >-
                  When false, suppress the initial state snapshot on subscribe
                  and stream only live updates. Defaults to true.
                required: false
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
              x-parser-schema-id: <anonymous-schema-217>
            channel:
              type: string
              const: exchangeAttributeUpdates
              x-parser-schema-id: <anonymous-schema-218>
            snapshot:
              type: boolean
              description: >-
                When false, suppress the initial state snapshot on subscribe and
                stream only live updates. Defaults to true.
              default: true
              x-parser-schema-id: <anonymous-schema-219>
          x-parser-schema-id: SubscribeExchangeAttributeUpdatesPayload
        title: Subscribe to the exchange attribute updates channel
        example: |-
          {
            "type": "subscribe",
            "channel": "exchangeAttributeUpdates"
          }
        bindings: []
        extensions:
          - id: x-parser-unique-object-id
            value: subscribe
    bindings: []
    extensions: &ref_2
      - id: x-parser-unique-object-id
        value: exchangeAttributeUpdates
  - &ref_4
    id: receiveExchangeAttributes
    title: Receive exchange attributes
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
                      - entries
                    properties:
                      isSnapshot:
                        type: boolean
                        description: >
                          True for the subscribe-time snapshot; false for
                          streaming updates triggered by operator fee tier
                          edits.
                        x-parser-schema-id: <anonymous-schema-222>
                      entries:
                        type: array
                        items:
                          type: object
                          description: >
                            A discriminated-union entry in the
                            exchangeAttributeUpdates stream, discriminated by
                            `type`: `"feeTierConfig"` (full fee tier table) or
                            `"marketAttributes"` (per-market RTH / VWAP-seal /
                            trading-bound edge events — DEPRECATED, superseded
                            by the dedicated `marketAttributes` channel whose
                            entries carry the market's full attribute state).
                          required:
                            - type
                          properties:
                            type:
                              type: string
                              enum:
                                - feeTierConfig
                                - marketAttributes
                              description: >-
                                Discriminator. `"feeTierConfig"` carries the
                                full fee tier table; `"marketAttributes"`
                                carries one per-market RTH or trading-bound
                                event.
                              x-parser-schema-id: <anonymous-schema-224>
                            tiers:
                              type: array
                              items:
                                type: object
                                description: >-
                                  One tier row in a feeTierConfig exchange
                                  attribute entry.
                                required:
                                  - level
                                  - name
                                  - volumeThreshold
                                  - makerFeePpm
                                  - takerFeePpm
                                properties:
                                  level:
                                    type: integer
                                    format: uint8
                                    description: >
                                      Tier level index (0=Base, 1=Bronze,
                                      2=Silver, 3=Gold, 4=Platinum, 5=VIP,
                                      6=Max).
                                    x-parser-schema-id: <anonymous-schema-226>
                                  name:
                                    type: string
                                    description: >-
                                      Human-readable tier name (e.g. "Base",
                                      "Gold").
                                    x-parser-schema-id: <anonymous-schema-227>
                                  volumeThreshold:
                                    type: integer
                                    format: int64
                                    description: >
                                      30-day notional volume threshold in
                                      USD×1e6 required to reach this tier.
                                      Always 0 for the base tier.
                                    x-parser-schema-id: <anonymous-schema-228>
                                  makerFeePpm:
                                    type: integer
                                    format: int32
                                    description: >
                                      Maker fee rate in ppm — parts-per-million
                                      of notional (e.g. 200 = 0.02% = 2 bps).
                                      Negative values indicate a maker rebate.
                                    x-parser-schema-id: <anonymous-schema-229>
                                  takerFeePpm:
                                    type: integer
                                    format: int32
                                    description: >
                                      Taker fee rate in ppm — parts-per-million
                                      of notional (e.g. 500 = 0.05% = 5 bps).
                                      Always ≥ 0.
                                    x-parser-schema-id: <anonymous-schema-230>
                                x-parser-schema-id: ExchangeAttributesFeeTierTier
                              description: >
                                Present when `type` is `"feeTierConfig"`. Full
                                replacement of the exchange fee tier table,
                                sorted ascending by level.
                              x-parser-schema-id: <anonymous-schema-225>
                            marketId:
                              type: integer
                              description: >-
                                Numeric market id (marketAttributes entries
                                only).
                              x-parser-schema-id: <anonymous-schema-231>
                            marketDisplayName:
                              type: string
                              description: Market ticker (marketAttributes entries only).
                              example: INTC-USD
                              x-parser-schema-id: <anonymous-schema-232>
                            isOutsideRth:
                              type: boolean
                              description: >-
                                Regular Trading Hours state after the event
                                (marketAttributes entries only).
                              x-parser-schema-id: <anonymous-schema-233>
                            sealedVwapSettlementPrice:
                              type: string
                              description: >-
                                Sealed VWAP settlement price (decimal USD)
                                anchoring the off-hours trading band
                                (marketAttributes entries only; omitted when the
                                frame carries no seal).
                              example: '23.41'
                              x-parser-schema-id: <anonymous-schema-234>
                            boundEvent:
                              type: string
                              enum:
                                - exitRth
                                - enterRth
                                - expansionZoneEntered
                                - expansionZoneExited
                                - tradingBoundExpanded
                              description: >-
                                Event kind (marketAttributes entries only;
                                always present on these entries). RTH
                                transitions: `exitRth` (market leaves regular
                                trading hours) / `enterRth` (market re-enters).
                                Off-hours bound events: `expansionZoneEntered`
                                (side's impact-price EWMA crossed within 90% of
                                its trading boundary, starting the 1-hour
                                expansion clock), `expansionZoneExited` (EWMA
                                relaxed out before the hour completed; clock
                                reset), `tradingBoundExpanded` (clock completed;
                                band widened one level on the 0.5×→1×→2×→4×
                                ladder). Every entry also carries the per-side
                                zone state (`upperZoneEnteredAt` /
                                `upperExpectedExpansionAt` /
                                `lowerZoneEnteredAt` /
                                `lowerExpectedExpansionAt`), mirrored on `GET
                                /v1/markets`. (Historical frames may carry the
                                retired `boundHit` / `boundExpand` values.)
                              x-parser-schema-id: <anonymous-schema-235>
                            boundSide:
                              type: string
                              enum:
                                - upper
                                - lower
                              description: >-
                                Which band edge fired on bound-event deltas
                                ("upper" / "lower"). Null on RTH-transition
                                (`exitRth` / `enterRth`) and VWAP-seal deltas.
                              x-parser-schema-id: <anonymous-schema-236>
                            boundPrice:
                              type: string
                              description: >-
                                Actual boundary price (decimal USD) in force
                                when the bound event fired — the boundary being
                                pressured for a zone event, the pre-expansion
                                boundary for a `tradingBoundExpanded`.
                              example: '13.52'
                              x-parser-schema-id: <anonymous-schema-237>
                            newBoundPrice:
                              type: string
                              description: >-
                                Boundary price (decimal USD) after the bound
                                event — equals `boundPrice` for zone events, the
                                widened boundary for a `tradingBoundExpanded`.
                              example: '12.02'
                              x-parser-schema-id: <anonymous-schema-238>
                            upperZoneEnteredAt:
                              type: integer
                              format: int64
                              description: >-
                                Unix seconds at which the upper side entered its
                                expansion zone, carried on every
                                marketAttributes entry. Omitted when the upper
                                side is not in its zone.
                              x-parser-schema-id: <anonymous-schema-239>
                            upperExpectedExpansionAt:
                              type: integer
                              format: int64
                              description: >-
                                Unix seconds of the earliest time the upper
                                bound can expand (zoneEnteredAt + one hour; not
                                a guarantee — the EWMA must still be in the zone
                                when the hour completes). Omitted when the upper
                                side is not in its zone.
                              x-parser-schema-id: <anonymous-schema-240>
                            lowerZoneEnteredAt:
                              type: integer
                              format: int64
                              description: >-
                                Unix seconds at which the lower side entered its
                                expansion zone. Omitted when the lower side is
                                not in its zone.
                              x-parser-schema-id: <anonymous-schema-241>
                            lowerExpectedExpansionAt:
                              type: integer
                              format: int64
                              description: >-
                                Unix seconds of the earliest time the lower
                                bound can expand. Omitted when the lower side is
                                not in its zone.
                              x-parser-schema-id: <anonymous-schema-242>
                            timestamp:
                              type: integer
                              format: int64
                              description: >-
                                Event time in Unix microseconds
                                (marketAttributes entries only).
                              x-parser-schema-id: <anonymous-schema-243>
                            marketSequenceNum:
                              type: integer
                              format: int64
                              description: >-
                                Per-market transaction sequence number
                                (marketAttributes entries only). One engine
                                transaction can emit several entries for a
                                market (e.g. both bound sides transitioning on
                                one tick) and they share this value — it
                                identifies the transaction, not the individual
                                entry, so do not dedup entries by it alone.
                              x-parser-schema-id: <anonymous-schema-244>
                          x-parser-schema-id: ExchangeAttributesEntry
                        x-parser-schema-id: <anonymous-schema-223>
                    x-parser-schema-id: <anonymous-schema-221>
                x-parser-schema-id: <anonymous-schema-220>
            x-parser-schema-id: ExchangeAttributesUpdatePayload
            name: Exchange Attributes Snapshot
            description: >
              Initial snapshot sent once on subscribe. Contains one
              `feeTierConfig` entry with the complete current fee tier table
              (`isSnapshot: true`).
        headers: []
        jsonPayloadSchema: &ref_1
          allOf: *ref_0
          x-parser-schema-id: ExchangeAttributesUpdatePayload
        title: Exchange Attributes Snapshot
        description: >
          Initial snapshot sent once on subscribe. Contains one `feeTierConfig`
          entry with the complete current fee tier table (`isSnapshot: true`).
        example: |-
          {
            "type": "subscribed",
            "channel": "exchangeAttributeUpdates",
            "id": "",
            "contents": {
              "isSnapshot": true,
              "entries": [
                {
                  "type": "feeTierConfig",
                  "tiers": [
                    {
                      "level": 0,
                      "name": "Base",
                      "volumeThreshold": 0,
                      "makerFeePpm": 200,
                      "takerFeePpm": 500
                    },
                    {
                      "level": 1,
                      "name": "Bronze",
                      "volumeThreshold": 100000000000,
                      "makerFeePpm": 150,
                      "takerFeePpm": 450
                    }
                  ]
                }
              ]
            }
          }
        bindings: []
        extensions:
          - id: x-parser-unique-object-id
            value: snapshot
      - &ref_7
        id: update
        contentType: application/json
        payload:
          - allOf: *ref_0
            x-parser-schema-id: ExchangeAttributesUpdatePayload
            name: Exchange Attributes Update
            description: >
              Streaming update pushed whenever an operator edits the exchange
              fee tier config. Always carries the full updated table in a single
              `feeTierConfig` entry (`isSnapshot: false`). Clients should
              replace their local copy atomically.
        headers: []
        jsonPayloadSchema: *ref_1
        title: Exchange Attributes Update
        description: >
          Streaming update pushed whenever an operator edits the exchange fee
          tier config. Always carries the full updated table in a single
          `feeTierConfig` entry (`isSnapshot: false`). Clients should replace
          their local copy atomically.
        example: |-
          {
            "type": "channel_data",
            "channel": "exchangeAttributeUpdates",
            "id": "",
            "contents": {
              "isSnapshot": false,
              "entries": [
                {
                  "type": "feeTierConfig",
                  "tiers": [
                    {
                      "level": 0,
                      "name": "Base",
                      "volumeThreshold": 0,
                      "makerFeePpm": 200,
                      "takerFeePpm": 500
                    }
                  ]
                }
              ]
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
    value: exchangeAttributeUpdates
securitySchemes: []

````

This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.