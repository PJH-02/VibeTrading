# Crypto collection pause diagnosis

Observed on production host 49.50.141.106, 2026-10-01 20:55–20:59 KST. Read-only investigation; no service restart or configuration change.

## Confirmed trigger

Read-only collection gap ledger records show:

| Provider | Pause began (KST) | Gap closed (KST) | Reason |
|---|---|---|---|
| Bitget | 20:54:04.052 | 20:57:05.838 | nats_stream_capacity |
| Bybit | 20:54:06.382 | 20:57:02.741 | nats_stream_capacity |
| Binance | 20:54:20.111 | 20:57:23.834 | nats_stream_capacity |

The gap closes after sustained publishing recovery; collector logs record pressure recovery at 20:55:45 (Bitget), 20:55:42 (Bybit), and 20:55:47 (Binance).

Verified deployed policy pauses at stream usage >=80% and resumes after usage <50%, sufficient disk headroom, and a 60-second stability window. CRYPTO_DATA is a file-backed workqueue with one storage consumer and max_bytes=134217728 (128 MiB). Stop threshold is 102.4 MiB; resume threshold is below 64 MiB. The exact stream byte count at the original pause was not retained in the inspected ledger.

Collector publisher counters at the capacity pause show failed=0 and unsent_events=0 for all three providers. Containers remain running, OOMKilled=false, and have no automatic Docker restarts. All three are healthy at the final check.

## Throughput and resource evidence

At 20:57:31.971–20:58:02.328 KST:

- CRYPTO_DATA sequence advanced 7,056 in about 30.36 seconds: approximately 232 events/s.
- Retained messages increased from 6,497 to 7,264. With workqueue retention, the implied removed/acknowledged count is 6,289, approximately 207 events/s.
- Stream bytes grew from 13,007,627 to 17,665,112 (12.4 to 16.8 MiB), approximately 4.4 MiB growth.
- Consumer num_pending=0 at both endpoints; num_ack_pending increased from 6,496 to 7,263. This distinguishes outstanding durable-save/ACK work from an idle consumer.
- Storage heartbeats show continued message and ACK progress; last_commit_ms rose from 477 to 1,605. Heartbeat counter intervals differ from the external measurement interval and should not be used directly as 30-second rates.
- A separate 10.47-second host sample at approximately 20:55:56–20:56:06 found data-disk utilization 99.13%, write await 91.09 ms, and host full I/O pressure avg60=61.75%. During the later sample full I/O pressure avg60 remained 51–56%.
- MARKET_DATA also had about 104,000 undelivered messages. Both pipelines share the disk.
- Available disk space was approximately 5.4 GB, above the 4 GiB reserve and 512 MiB resume margin. The immediate capacity stop was not disk_reserve, although an earlier 20:25 stop had that reason.

The evidence supports an intentional capacity stop caused by ingress temporarily exceeding durable storage/ACK throughput. Disk I/O contention is the strongest observed bottleneck. It does not establish that exchange traffic suddenly increased at 20:54: no same-window historical ingress baseline was available. The earlier 17:03–17:04 observation recorded 10,874 trade/book messages in 30 seconds (~362/s), but is a different time and should not be used to prove the pause-time rate.

## Separate error

Binance logged RuntimeError: Binance SOLUSDT snapshot bridge failure at 20:56:00 KST, then retried and returned to publishing. It occurred after the capacity pause and cannot explain Bitget/Bybit stopping together. This is a separate order-book synchronization issue.

## Operational implication

Restarting collectors resets the in-process pause latch but does not remove the storage bottleneck. Capacity pauses can recur. Investigate shared-disk durable write/ACK latency, storage flush behavior, and competing MARKET_DATA work before increasing NATS limits or weakening the protection thresholds.
