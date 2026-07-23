# Demo Script — 25–28 phút

## Mục tiêu người trình bày

Kể một câu chuyện end-to-end, tập trung vào reliability và business value thay vì liệt kê công nghệ. Mỗi lần chuyển màn hình phải trả lời được ba câu: dữ liệu đến từ đâu, được bảo vệ thế nào, và người vận hành xác minh ở đâu.

Không hiển thị `.env`, password, full connection URI hoặc raw customer payload. Dùng dataset demo riêng và đóng các tab chứa thông tin cá nhân trước khi share screen.

Mode mặc định cho interview là **Prepared demo + một live CDC change nhỏ**. Nếu runtime không ổn
định, chuyển sang Offline fallback bằng screenshots và saved sanitized output; không mất thời gian
reset/rebuild giữa buổi trình bày.

Trước rehearsal, tạo namespace riêng:

```bash
export DEMO_ID=demo-20260723-01
export GENERATOR_SEED=9231
export CDC_CONSUMER_GROUP_ID=fintech-demo-20260723-01
export BACKFILL_REQUEST_ID="$(python -c 'import uuid; print(uuid.uuid4())')"
```

`DEMO_ID` là operator convention; generator seed, consumer group và backfill request ID là inputs
thực tế. Không reuse backfill UUID từ lần demo trước.

## Browser/editor tabs chuẩn bị sẵn

1. Architecture diagram trong `docs/demo/demo-guide.md`.
2. DBeaver: schema `payments`, saved query chỉ hiển thị IDs/status/counts.
3. MinIO Console: `fintech-bronze`, `fintech-quarantine`, `fintech-silver`.
4. Airflow UI: DAG list và Grid của `settlement_batch_pipeline`.
5. VS Code: `contracts/batch/settlement_v1.yml` và một Silver schema module.
6. Terminal 1: service health và sanitized CDC inspection.
7. Terminal 2: bounded consumer/Silver commands.

Kafka UI/AKHQ chưa được cài; dùng sanitized inspector. Nếu có DBeaver/VS Code ở máy demo thì đó là external tooling, không phải service của Compose.

## Kịch bản theo thời gian

### 00:00–02:00 — Business problem và architecture

**Màn hình:** architecture diagram.

**Nói:**

> Đây là data platform cho payment gateway, merchant payments, transfers, refunds và daily bank settlement. Platform có hai ingestion path: CDC từ PostgreSQL và batch CSV từ banking partners. Cả hai giữ raw evidence trong Bronze, được chuẩn hóa sang Silver và được Airflow điều phối.

Nhấn mạnh hai use case dài hạn:

- near-real-time payment operations;
- daily settlement reconciliation.

Chốt boundary: demo kết thúc tại Silver/control plane; chưa trình bày Snowflake, dbt hoặc Gold.

**Expected evidence:** người xem hiểu hai source paths và vì sao Bronze bất biến.

> Screenshot cue — Toàn bộ architecture diagram.

### 02:00–04:00 — Phase 0: engineering foundation

**Màn hình:** repository tree, CI workflow và roadmap.

**Command đã chạy trước hoặc chạy nhanh:**

```bash
make validate
```

**Nói:**

> Mỗi phase có module, test, runbook và acceptance boundary riêng. Ruff, format, Pytest, YAML và Compose validation là commit gate. Credentials chỉ đến từ environment variables.

Không đọc từng folder; chỉ chỉ ra `src`, `contracts`, `infrastructure`, `tests`, `airflow`, `docs`.

**Expected evidence:** validation thành công và scope Phase 0–7 rõ.

> Screenshot cue — CI checks xanh và roadmap đánh dấu current state.

### 04:00–07:00 — Phase 1: PostgreSQL business domain

**Màn hình:** DBeaver hoặc psql counts/status query.

**Command:**

```bash
make generate-data GENERATOR_ARGS="--once --seed ${GENERATOR_SEED} --customers 10 --merchants 5 --transactions 50 --invalid-rate 0 --duplicate-rate 0"
```

**Nói:**

> Generator dùng deterministic seed, Decimal và UTC timestamps. Database constraints bảo vệ status, currency, positive amount, uniqueness và relationships. `transaction_events` giữ lifecycle append-only, còn payment transaction là current state của OLTP.

Mở query counts rồi một danh sách transaction chỉ gồm ID, amount, currency, status, timestamp.

**Expected evidence:** related rows xuất hiện trong customers/accounts/merchants/payments/events/refunds.

> Screenshot cue — Entity counts và payment statuses, không hiển thị email/full name.

### 07:00–10:00 — Phase 2–3: settlement contract, Bronze và quarantine

**Màn hình:** settlement contract trong VS Code, sau đó MinIO Console.

**Command:**

```bash
make generate-settlement-fixtures SETTLEMENT_FIXTURE_SEED=42
make ingest-settlements-minio SETTLEMENT_INGEST_ARGS="--file data/inbound/settlements/settlement_VCB_2026-07-22_001.csv --partner-id VCB --contract contracts/batch/settlement_v1.yml"
```

**Nói:**

> Banking partner file được kiểm tra naming, SHA-256, schema và từng record. Valid raw source được giữ nguyên; record/file lỗi vào quarantine. Manifest phân biệt cùng tên-cùng nội dung, cùng tên-khác nội dung và khác tên-cùng nội dung.

Trong MinIO Console, click vào partitioned Bronze path và metadata. Nếu đã chuẩn bị quality fixture, mở quarantine object để chỉ ra error code/count, không mở raw PII.

**Expected evidence:** raw CSV trong `fintech-bronze`, quality artifact trong `fintech-quarantine`, private buckets và checksum metadata.

> Screenshot cue — Bronze object path cạnh quarantine path.

### 10:00–13:00 — Phase 4: Debezium CDC và Kafka

**Màn hình:** connector status và sanitized inspector.

**Command:**

```bash
make cdc-status
make generate-data GENERATOR_ARGS="--once --seed $((GENERATOR_SEED + 1)) --customers 1 --merchants 1 --transactions 5 --invalid-rate 0 --duplicate-rate 0"
make cdc-inspect CDC_TABLE=payment_transactions
```

**Nói:**

> PostgreSQL logical WAL được Debezium đọc qua least-privilege connector user. Mỗi table có topic theo `fintech.cdc.payments.<table>`. Debezium envelope giữ before/after, operation, source LSN và timestamps. Decimal mode là precise, không phải double.

Chỉ vào `op`, snapshot flag, LSN, partition và offset. Không in full value.

**Expected evidence:** connector/task `RUNNING`, event mới có Kafka coordinates và source metadata.

> Screenshot cue — Connector status và một sanitized event line.

### 13:00–16:00 — Phase 5: reliable CDC consumer

**Màn hình:** terminal consumer result, sau đó MinIO CDC Bronze object.

**Command:**

```bash
make cdc-consumer-once CDC_CONSUMER_ARGS="--storage-backend minio --group-id ${CDC_CONSUMER_GROUP_ID} --max-messages 100"
make inspect-cdc-bronze
```

Nói trước khi chạy: command dừng khi đủ 100 polled messages hoặc sau assignment có hai empty polls,
flush pending batches và in JSON summary; exit `0` là clean completion. Ctrl+C được consumer xử lý
gracefully nếu cần dừng.

**Nói:**

> Consumer tắt auto commit và không gọi đây là exactly-once. Một object chỉ chứa một topic-partition và contiguous offset range. Trình tự là serialize Parquet, upload, verify checksum/metadata, mark uploaded, commit offset_end + 1, rồi mark committed.

Giải thích crash case bằng một câu:

> Nếu upload thành công nhưng commit thất bại, Kafka replay; deterministic key và checksum biến replay thành idempotent success trước khi commit lại.

**Expected evidence:** immutable Parquet path có entity/topic/partition/offset range; metadata có checksum, group và flags.

> Screenshot cue — CDC Bronze object key và offset metadata.

### 16:00–20:00 — Phase 6: Silver semantics

**Màn hình:** MinIO `fintech-silver` và sanitized Silver inspector.

**Command:**

```bash
make silver-process-cdc SILVER_CDC_ARGS="--storage-backend minio --input-prefix cdc/ --max-objects 20"
make silver-process-settlements SILVER_SETTLEMENT_ARGS="--storage-backend minio --input-prefix settlements/ --max-objects 20"
make silver-inspect SILVER_INSPECT_ARGS="--storage-backend minio"
```

Nói trước khi chạy: mỗi processor discover tối đa 20 objects rồi thoát và in JSON
`discovered/results`; đây không phải long-running process. Không Ctrl+C giữa object write vì Silver
CLI không cài custom signal handler.

**Nói:**

> Silver không collapse mọi thứ thành current. History giữ audit grain; latest-all giữ latest row kể cả delete; current chỉ giữ active state. Kafka offset quyết định ordering trong partition. Decimal dùng Arrow decimal và timestamps là UTC. Invalid records có reason code thay vì bị drop im lặng.

Click lần lượt `history`, `latest_all`, `current`, `settlements`, `rejections`. Không download/open customer payload trên màn hình chia sẻ.

**Expected evidence:** explicit Parquet schemas, immutable run paths và processing manifest skip input đã xử lý.

> Screenshot cue — Ba state datasets và một rejection/error-code summary.

### 20:00–25:00 — Phase 7: Airflow orchestration/control plane

**Màn hình:** Airflow DAG list, Graph/Grid và task details.

**Command:**

```bash
make airflow-demo-login-info
make airflow-dags-list
make trigger-settlement-pipeline
make trigger-cdc-silver-pipeline
make trigger-backfill BACKFILL_CONF="{\"request_id\":\"${BACKFILL_REQUEST_ID}\",\"source_type\":\"CDC\",\"dry_run\":true}"
```

Các trigger command chỉ submit run rồi thoát; exit `0` nghĩa Airflow chấp nhận request, không có
nghĩa DAG đã hoàn thành. Theo dõi trạng thái trong Grid/UI. Password đã được lấy riêng bằng guarded
target trước khi share screen; không chạy password target trong script chính.

**Nói:**

> Airflow không chứa transformation logic và không chạy streaming consumer vô hạn. DAG gọi application services hiện hữu, truyền IDs/URIs/counts nhỏ qua XCom, dùng retries dựa trên idempotency của component.

Đi qua bốn DAG:

1. settlement discover → ingest → validate → Silver → quality;
2. CDC connector/group lag/freshness control;
3. entity-aware CDC Silver dependencies;
4. bounded backfill với explicit request ID và dry-run.

Mở một quality task và nói:

> PASS tiếp tục, WARN cho phép PARTIAL theo policy, FAIL chặn publish. Record-level rejection vẫn thuộc component dataset, control schema chỉ lưu operational summary.

**Expected evidence:** DAGs parse, task dependencies đúng, pipeline/control rows và quality classifications hiện diện.

> Screenshot cue — Airflow Graph/Grid, quality gate và dry-run backfill.

### 25:00–28:00 — Reliability recap và Q&A

**Màn hình:** architecture diagram hoặc một slide “failure matrix”.

Ba talking points:

1. **Idempotency:** checksum cho file/object, Kafka coordinates cho CDC, code+schema version cho Silver.
2. **Recovery:** source-specific manifests giữ fine-grained state; Airflow/control plane giữ orchestration state; retry không phá immutable outputs.
3. **Scale path:** current Python/PyArrow/MinIO logic chứng minh semantics trước; Snowflake/dbt/Gold là các phase riêng, không được giả vờ đã triển khai.

Câu kết:

> Platform hiện chứng minh được ingestion, durability, ordering, quality, recovery và orchestration end-to-end đến Silver; business marts và BI được cố ý để sau khi warehouse contract ổn định.

## Câu hỏi thường gặp

### Vì sao không gọi exactly-once?

Kafka commit và MinIO/SQLite manifest không nằm trong một distributed transaction. Thiết kế chấp nhận replay, dùng deterministic identity/checksum và commit upload-first để đạt effectively-once Bronze behavior.

### Vì sao Airflow không chạy consumer liên tục?

Airflow là batch orchestrator/control plane, không phải streaming runtime. Consumer chạy độc lập; Airflow đo connector health, group lag và freshness.

### Vì sao giữ history, latest-all và current?

History phục vụ audit/replay; latest-all bảo toàn delete state; current phục vụ active analytical view. Loại delete quá sớm sẽ phá audit và SCD2 downstream.

### Kafka UI ở đâu?

Chưa được cài. Demo hiện dùng sanitized inspection script và Airflow health DAG. AKHQ/Kafka UI là đề xuất demo tooling tương lai, không phải dependency Phase 0–7.

### Nếu command demo không có dữ liệu mới?

Dùng seed/group ID/input fixture dành riêng cho lần demo, hoặc tạo event mới bằng seed chưa dùng. Không reset toàn bộ volume trong lúc trình bày.

## Rollback nếu demo bị gián đoạn

- Giữ terminal health check và screenshot dự phòng.
- Nếu một bounded run đã xử lý input, giải thích idempotent skip và chuyển sang object đã chuẩn bị.
- Nếu UI chậm, dùng `docker compose --env-file .env ps`, `make cdc-status`, `make airflow-dags-list` và sanitized inspectors.
- Không chạy reset volume trong buổi demo. Dừng/restart đúng service bằng Make target tương ứng; named data vẫn được giữ.
