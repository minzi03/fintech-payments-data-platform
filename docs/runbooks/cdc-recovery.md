# CDC Consumer Recovery Runbook

## Recovery principle

Kafka is replayable and Bronze is immutable. Recovery never edits an object or guesses an offset.
Use topic/partition/range, manifest evidence, object checksum, and Kafka's committed next offset to
decide the next action. The system is effectively once at the object boundary, not exactly once.

## Crash matrix

| Failure point | Durable evidence | Kafka offset | Recovery action |
| --- | --- | --- | --- |
| Before manifest registration | None | Not advanced | Poll/rebuild normally |
| During serialization | `COLLECTING`/`SERIALIZING` | Not advanced | Re-serialize with stable manifest timestamp |
| During upload | `UPLOADING` or `FAILED` | Not advanced | Retry deterministic conditional write |
| Object exists; manifest update failed | Object present, manifest `FAILED`/`UPLOADING` | Not advanced | Re-serialize, verify same checksum, promote `UPLOADED` |
| Manifest `UPLOADED`; Kafka commit failed | Object + checksum + URI | Not advanced | Replay, verify object, retry `offset_end + 1` |
| Kafka commit succeeded; manifest commit failed | Object + manifest `UPLOADED` | Already advanced | Assignment compares broker offset and promotes `COMMITTED` |
| Manifest `COMMITTED`; record replayed | Object + checksum + URI | May be behind after group reset | Verify object, commit same next offset again |
| Poison quarantine failed | No confirmed quarantine evidence | Not advanced | Repair quarantine access and retry same source record |
| Poison quarantine succeeded; commit failed | Group-scoped immutable DLQ object | Not advanced | Idempotently reuse DLQ object and retry source commit |
| Same key, different checksum | Conflicting immutable evidence | Not advanced | Stop and investigate; never overwrite |

## Standard replay procedure

1. Stop the consumer cleanly and record group, topic, partition, and affected range.
2. Run payload-safe manifest inspection and connector health checks.
3. Confirm the Bronze object exists at the manifest URI and its SHA-256 equals the manifest.
4. Inspect Kafka's committed offset for only the affected group/partition.
5. If Kafka is at or beyond `offset_end + 1`, restart once so assignment reconciles `UPLOADED` to
   `COMMITTED`.
6. If Kafka is behind, restart with the same group. Replay reconstructs the same batch/object key,
   verifies checksum, commits `offset_end + 1`, then marks `COMMITTED`.
7. Confirm no second object exists for the range and inspection reports the expected row count.

Never manually mark `COMMITTED` without broker evidence. Never move a group past an unverified
object, and never resolve collision by overwriting Bronze.

## Offset and object evidence

```bash
python -m ingestion.cdc_consumer.cli inspect --storage-backend minio
docker compose --env-file .env exec kafka \
  /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 \
  --describe --group fintech-cdc-bronze-v1
```

Use the local MinIO runbook to stat/download the object. Do not print Parquet customer columns during
incident handling; compare length, metadata, and SHA-256 first.

## Development reset

`make reset-cdc-consumer-state CONFIRM=1` is intentionally destructive and local only. It removes
the named group offsets and dedicated consumer manifest/temp volumes, but leaves source/transport/
object volumes intact. A subsequent `earliest` run may replay the full retained log and should reuse
identical immutable objects. This is not a production recovery procedure.

## Escalation conditions

Escalate instead of retrying when checksum collision, non-contiguous range, schema version conflict,
missing object for an advanced Kafka offset, or corruption is observed. Preserve manifest database,
Kafka group output, object metadata, and connector status as evidence. Raw payloads are confidential
and must not be copied into logs or tickets.
