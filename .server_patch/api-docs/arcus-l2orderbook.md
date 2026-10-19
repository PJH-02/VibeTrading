> ## Documentation Index
> Fetch the complete documentation index at: https://docs.arcus.xyz/llms.txt
> Use this file to discover all available pages before exploring further.

# L2 orderbook

> > **Endpoint:** `wss://api.arcus.xyz/v1/ws` — one socket multiplexes every channel and RPC request. Click **Connect**, then send the example subscribe message below to start receiving data.

Periodic full L2 orderbook snapshots for a single market. On subscribe, the server sends the current book up to `nLevels` price levels per side, then continues to broadcast a fresh full snapshot every ~200ms.

No incremental updates — each message replaces the prior book state. Use `l2OrderbookUpdates` if you need to maintain your own local book.

A connection may subscribe more than once to the same market when each subscribe uses a distinct (`sigFigs`, `roundStep`) pair. `subscribed`, `channel_data`, and `unsubscribed` frames echo those fields (omitted for full precision) so the client can demux views. Unsubscribe must include the same pair to drop one view without affecting the others.




## AsyncAPI

````yaml api-reference/asyncapi.yaml l2Orderbook
id: l2Orderbook
title: L2 orderbook
description: >
  > **Endpoint:** `wss://api.arcus.xyz/v1/ws` — one socket multiplexes every
  channel and RPC request. Click **Connect**, then send the example subscribe
  message below to start receiving data.


  Periodic full L2 orderbook snapshots for a single market. On subscribe, the
  server sends the current book up to `nLevels` price levels per side, then
  continues to broadcast a fresh full snapshot every ~200ms.


  No incremental updates — each message replaces the prior book state. Use
  `l2OrderbookUpdates` if you need to maintain your own local book.


  A connection may subscribe more than once to the same market when each
  subscribe uses a distinct (`sigFigs`, `roundStep`) pair. `subscribed`,
  `channel_data`, and `unsubscribed` frames echo those fields (omitted for full
  precision) so the client can demux views. Unsubscribe must include the same
  pair to drop one view without affecting the others.
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
address: /v1/ws?channel=l2Orderbook
parameters: []
bindings: []
operations:
  - &ref_2
    id: subscribeL2Orderbook
    title: Subscribe l2 orderbook
    type: receive
    messages:
      - &ref_4
        id: subscribe
        contentType: application/json
        payload:
          - name: Subscribe to L2 Orderbook
            type: object
            properties:
              - name: type
                type: string
                description: subscribe
                required: true
              - name: channel
                type: string
                enumValues:
                  - l2Orderbook
                  - l2OrderbookUpdates
                required: true
              - name: id
                type: string
                description: Market display name (e.g. "BTC-USD", "ETH-USD").
                required: true
              - name: nLevels
                type: integer
                description: Price levels per side in snapshot. Range 1–100.
                required: false
              - name: sigFigs
                type: integer
                description: >
                  Aggregate price levels to this many significant figures. Omit
                  for full precision. Bids round down / asks round up to the
                  bucket boundary and merged sizes are summed; applied before
                  nLevels truncation. A second subscribe on the same market is
                  allowed when this (with roundStep) differs from every view
                  already held.
                enumValues:
                  - 2
                  - 3
                  - 4
                  - 5
                required: false
              - name: roundStep
                type: integer
                description: >
                  Refines the finest bucket into steps of 1, 2, or 5. Takes
                  effect only when sigFigs is 5; ignored otherwise. Unsubscribe
                  must echo the same value used to subscribe.
                enumValues:
                  - 1
                  - 2
                  - 5
                required: false
        headers: []
        jsonPayloadSchema:
          type: object
          required:
            - type
            - channel
            - id
          properties:
            type:
              type: string
              const: subscribe
              x-parser-schema-id: <anonymous-schema-1>
            channel:
              type: string
              enum:
                - l2Orderbook
                - l2OrderbookUpdates
              x-parser-schema-id: <anonymous-schema-2>
            id:
              type: string
              description: Market display name (e.g. "BTC-USD", "ETH-USD").
              example: BTC-USD
              x-parser-schema-id: <anonymous-schema-3>
            nLevels:
              type: integer
              description: Price levels per side in snapshot. Range 1–100.
              default: 20
              minimum: 1
              maximum: 100
              x-parser-schema-id: <anonymous-schema-4>
            sigFigs:
              type: integer
              description: >
                Aggregate price levels to this many significant figures. Omit
                for full precision. Bids round down / asks round up to the
                bucket boundary and merged sizes are summed; applied before
                nLevels truncation. A second subscribe on the same market is
                allowed when this (with roundStep) differs from every view
                already held.
              enum:
                - 2
                - 3
                - 4
                - 5
              x-parser-schema-id: <anonymous-schema-5>
            roundStep:
              type: integer
              description: >
                Refines the finest bucket into steps of 1, 2, or 5. Takes effect
                only when sigFigs is 5; ignored otherwise. Unsubscribe must echo
                the same value used to subscribe.
              enum:
                - 1
                - 2
                - 5
              x-parser-schema-id: <anonymous-schema-6>
          x-parser-schema-id: SubscribeL2OrderbookPayload
        title: Subscribe to L2 Orderbook
        example: |-
          {
            "type": "subscribe",
            "channel": "l2Orderbook",
            "id": "BTC-USD",
            "nLevels": 50
          }
        bindings: []
        extensions:
          - id: x-parser-unique-object-id
            value: subscribe
    bindings: []
    extensions: &ref_1
      - id: x-parser-unique-object-id
        value: l2Orderbook
  - &ref_3
    id: receiveL2Orderbook
    title: Receive l2 orderbook
    type: send
    messages:
      - &ref_5
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
                  type:
                    const: subscribed
                    x-parser-schema-id: <anonymous-schema-16>
                  contents:
                    type: object
                    required:
                      - bids
                      - asks
                      - lastSequenceId
                    properties:
                      bids:
                        type: array
                        description: >-
                          Price levels, best bid first. Each entry is `[price,
                          size]`.
                        items:
                          type: array
                          minItems: 2
                          maxItems: 2
                          items:
                            type: string
                            x-parser-schema-id: <anonymous-schema-20>
                          x-parser-schema-id: <anonymous-schema-19>
                        example:
                          - - '94500.0'
                            - '1.5'
                          - - '94499.0'
                            - '2.0'
                        x-parser-schema-id: <anonymous-schema-18>
                      asks:
                        type: array
                        description: >-
                          Price levels, best ask first. Each entry is `[price,
                          size]`.
                        items:
                          type: array
                          minItems: 2
                          maxItems: 2
                          items:
                            type: string
                            x-parser-schema-id: <anonymous-schema-23>
                          x-parser-schema-id: <anonymous-schema-22>
                        example:
                          - - '94501.0'
                            - '0.8'
                          - - '94502.0'
                            - '3.1'
                        x-parser-schema-id: <anonymous-schema-21>
                      lastSequenceId:
                        type: integer
                        description: >-
                          Per-market orderbook sequence of the last update
                          reflected in this snapshot. Reconcile
                          `l2OrderbookUpdates` deltas against this — apply
                          deltas whose `lastSequenceId` exceeds it.
                        example: 1042
                        x-parser-schema-id: <anonymous-schema-24>
                      globalSequenceId:
                        type: integer
                        description: Cross-market monotonic sequence. Omitted when zero.
                        example: 880421
                        x-parser-schema-id: <anonymous-schema-25>
                      timestamp:
                        type: integer
                        format: int64
                        description: >-
                          Unix microseconds when the snapshot was taken. Omitted
                          when zero.
                        example: 1780410803123456
                        x-parser-schema-id: <anonymous-schema-26>
                    x-parser-schema-id: <anonymous-schema-17>
                x-parser-schema-id: <anonymous-schema-15>
            x-parser-schema-id: L2OrderbookSnapshotPayload
            name: L2 Orderbook Snapshot
        headers: []
        jsonPayloadSchema:
          allOf: *ref_0
          x-parser-schema-id: L2OrderbookSnapshotPayload
        title: L2 Orderbook Snapshot
        example: |-
          {
            "type": "subscribed",
            "channel": "l2Orderbook",
            "id": "BTC-USD",
            "contents": {
              "bids": [
                [
                  "94500.0",
                  "1.5"
                ],
                [
                  "94499.0",
                  "2.0"
                ]
              ],
              "asks": [
                [
                  "94501.0",
                  "0.8"
                ],
                [
                  "94502.0",
                  "3.1"
                ]
              ],
              "lastSequenceId": 1042,
              "globalSequenceId": 880421,
              "timestamp": 1780410803123456
            }
          }
        bindings: []
        extensions:
          - id: x-parser-unique-object-id
            value: snapshot
    bindings: []
    extensions: *ref_1
sendOperations:
  - *ref_2
receiveOperations:
  - *ref_3
sendMessages:
  - *ref_4
receiveMessages:
  - *ref_5
extensions:
  - id: x-parser-unique-object-id
    value: l2Orderbook
securitySchemes: []

````

This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.