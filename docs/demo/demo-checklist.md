# Demo Checklist — Phase 0 đến Phase 7

## 1. Trước ngày demo

- [ ] Đã đọc lại `docs/demo/demo-guide.md` và rehearsed `docs/demo/demo-script.md` trong 25–28 phút.
- [ ] Đã chọn rõ Prepared, Live incremental hoặc Offline fallback mode.
- [ ] Interview flow dùng Prepared demo + tối đa một live CDC change nhỏ.
- [ ] Offline screenshots và saved sanitized output đã sẵn sàng.
- [ ] Working tree/branch dùng để demo đã được ghi nhận; không vô tình trình chiếu diff chưa commit.
- [ ] `.env` tồn tại, bị Git ignore và không chứa credential dùng ngoài local demo.
- [ ] Tất cả placeholder `change_me`/Airflow secret đã được thay bằng giá trị local riêng.
- [ ] `git ls-files .env data tmp .coverage .pytest_cache .ruff_cache .venv` không trả runtime/secret files.
- [ ] `make validate` thành công.
- [ ] Docker Desktop đủ CPU, memory và disk cho PostgreSQL, MinIO, Kafka, Connect và Airflow.
- [ ] Đã dành startup buffer theo local estimate; không trình bày estimate như production SLA.
- [ ] Các host ports đã sẵn sàng hoặc URL trong notes đã cập nhật theo `.env`.
- [ ] Browser, terminal và editor dùng font/zoom dễ đọc khi share screen.
- [ ] Không có notification, password manager popup hoặc tab nhạy cảm trên màn hình chia sẻ.

## 2. Service startup và health

- [ ] Đã chọn cold start hay warm start; không rebuild/re-init theo thói quen.
- [ ] Warm start dùng `docker compose --env-file .env up -d`, không dùng `docker compose start`.
- [ ] PostgreSQL đã khởi động: `make postgres-up`.
- [ ] MinIO và bucket bootstrap đã chạy: `make minio-up`.
- [ ] Kafka, Kafka Connect và connector bootstrap đã chạy: `make cdc-up`.
- [ ] Airflow image đã build: `make airflow-build`.
- [ ] Airflow metadata/control schema đã init: `make airflow-init`.
- [ ] Airflow scheduler/UI/DAG processor đã chạy: `make airflow-up`.
- [ ] CDC consumer đã chọn rõ mode: bounded `make cdc-consumer-once` hoặc Compose profile continuous.
- [ ] `docker compose --env-file .env ps` cho thấy các long-running services healthy.
- [ ] PostgreSQL healthy.
- [ ] MinIO endpoint `/minio/health/ready` healthy.
- [ ] Kafka healthy.
- [ ] Kafka Connect endpoint đáp ứng.
- [ ] Debezium connector và task `RUNNING`: `make cdc-status`.
- [ ] Consumer healthy/runnable; không có crash loop.
- [ ] Airflow API health đáp ứng tại `/api/v2/monitor/health`.
- [ ] Airflow scheduler healthy.
- [ ] Airflow DAG processor healthy.
- [ ] One-shot services `minio-init`, `connector-init`, `airflow-init` completed successfully.

## 3. Dữ liệu demo

- [ ] `DEMO_ID`, `GENERATOR_SEED`, `CDC_CONSUMER_GROUP_ID`, `DEMO_DATE` đã được ghi trong private operator notes.
- [ ] `BACKFILL_REQUEST_ID` là UUID mới; không reuse UUID hard-coded từ rehearsal trước.
- [ ] Đã ghi rõ `SETTLEMENT_SEQUENCE` chỉ là operator convention và fixture generator không có `--sequence`.
- [ ] Không dùng `SILVER_RUN_NAMESPACE`; implementation hiện tự sinh Silver run ID.
- [ ] Sample OLTP data đã sẵn sàng bằng deterministic seed dành riêng cho demo.
- [ ] Query DBeaver/psql chỉ chọn ID, status, amount và timestamps; không hiển thị email/full name.
- [ ] Settlement fixture VCB ngày `2026-07-22` đã tạo.
- [ ] Valid settlement file đã sẵn sàng cho happy path.
- [ ] Partial-invalid/file-invalid fixture đã sẵn sàng cho quarantine story.
- [ ] Kafka topics đã có snapshot hoặc event history.
- [ ] Có seed chưa dùng để tạo live CDC events.
- [ ] CDC consumer group và manifest state được ghi nhận; biết bounded run sẽ process hay idempotently skip.
- [ ] MinIO Bronze đã có ít nhất một settlement raw object.
- [ ] MinIO Bronze đã có ít nhất một CDC Parquet object.
- [ ] MinIO quarantine đã có quality artifact minh họa, hoặc có screenshot dự phòng.
- [ ] MinIO Silver đã có history, latest-all, current và settlement outputs.
- [ ] Có rejection/unresolved-reference evidence nếu muốn trình bày data quality.

## 4. UI và browser tabs

- [ ] Airflow UI mở tại `http://localhost:${AIRFLOW_WEB_PORT}`; mặc định `http://localhost:8080`.
- [ ] `make airflow-demo-login-info` chỉ in URL, username và private retrieval instruction.
- [ ] Airflow generated password đã được lấy riêng bằng `make airflow-show-demo-password CONFIRM=1`.
- [ ] Terminal chứa password đã đóng/clear trước khi share screen.
- [ ] Password không nằm trong notes, screenshot hoặc command history.
- [ ] MinIO Console mở tại `http://localhost:${MINIO_CONSOLE_PORT}`; mặc định `http://localhost:9001`.
- [ ] MinIO credential chỉ được nhập từ ignored `.env`, không hiển thị khi share.
- [ ] Airflow DAG list hiển thị bốn DAG IDs.
- [ ] Airflow Grid/Graph có ít nhất một completed run hoặc screenshot dự phòng.
- [ ] MinIO Console mở sẵn `fintech-bronze`, `fintech-quarantine`, `fintech-silver`.
- [ ] Kafka Connect status được mở bằng sanitized REST/CLI output; nhớ rằng đây không phải UI.
- [ ] DBeaver kết nối PostgreSQL local nếu sử dụng.
- [ ] VS Code mở architecture, settlement contract và Silver schema cần trình bày.
- [ ] Nếu không có Kafka UI/AKHQ, không để agenda hứa hẹn màn hình Kafka UI.

## 5. Commands và artifacts chuẩn bị sẵn

- [ ] Terminal 1: `docker compose --env-file .env ps`, `make cdc-status`, `make airflow-dags-list`.
- [ ] Terminal 2: generator, batch ingestion, CDC consumer và Silver bounded commands.
- [ ] SQL query counts đã lưu trong DBeaver/clipboard an toàn.
- [ ] SQL demo không chứa hard-coded password hoặc production identifiers.
- [ ] Settlement happy-path command trỏ đúng một valid file.
- [ ] Quality-path command được ghi chú rằng intentional invalid fixture có thể trả non-zero exit.
- [ ] CDC inspector mặc định không in full payload/PII.
- [ ] Silver inspector dùng sanitized summaries.
- [ ] Airflow trigger commands và backfill dry-run JSON đã được validate.
- [ ] CDC bounded command có `--max-messages` và expected idle/assignment stop đã được nói trước.
- [ ] Silver commands có `--max-objects` và expected structured JSON completion đã được nói trước.
- [ ] Người trình bày biết Airflow trigger exit `0` chỉ xác nhận submission, không xác nhận DAG success.
- [ ] Không dự định Ctrl+C Silver giữa object write; CDC consumer mới có graceful signal handler.
- [ ] Screenshot placeholders đã có screenshot dự phòng nếu demo offline.
- [ ] Demo rollback notes đã mở sẵn.

## 6. Security/privacy gate ngay trước share screen

- [ ] `.env` đóng và không nằm trong recent-file preview.
- [ ] Không có terminal history đang hiển thị password/connection URI.
- [ ] Không mở raw customer `before_json`, `after_json`, Parquet hoặc DLQ payload.
- [ ] MinIO object metadata không chứa credentials hoặc absolute local path.
- [ ] Airflow XCom đang xem chỉ có ID, URI, count hoặc metadata nhỏ.
- [ ] Browser dev tools/network tab không hiển thị Authorization header.
- [ ] DBeaver password field không hiện plaintext.
- [ ] Screenshot/recording không thu notification hoặc clipboard manager.

## 7. Flow rehearsal

- [ ] 00:00 architecture/business use cases.
- [ ] 02:00 foundation/quality gates.
- [ ] 04:00 PostgreSQL/generator.
- [ ] 07:00 settlement contract/Bronze/quarantine.
- [ ] 10:00 Debezium/Kafka CDC.
- [ ] 13:00 reliable CDC consumer.
- [ ] 16:00 Silver history/latest-all/current.
- [ ] 20:00 Airflow orchestration/control plane.
- [ ] 25:00 reliability recap/Q&A.
- [ ] Tổng thời lượng rehearsal nằm trong 20–30 phút.

## 8. Evidence checklist theo phase

- [ ] Phase 0: repository boundaries và validation output.
- [ ] Phase 1: related OLTP counts, Decimal/status/timestamp evidence.
- [ ] Phase 2: versioned contract, SHA-256 manifest và quarantine behavior.
- [ ] Phase 3: private MinIO buckets, immutable object path/metadata.
- [ ] Phase 4: connector/task RUNNING, topic convention, op/LSN/partition/offset.
- [ ] Phase 5: upload-before-commit, deterministic range object và committed manifest.
- [ ] Phase 6: history/latest-all/current, Decimal/UTC, rejection reason code.
- [ ] Phase 7: DAG dependencies, quality classification, control row và backfill dry-run.

## 9. Rollback/fallback

- [ ] Đã xác định restart command cho từng service, không dùng `down -v`.
- [ ] Không chạy `postgres-reset`, `minio-reset`, `reset-airflow-metadata`, `reset-cdc-consumer-state` hoặc `reset-silver-state` trong demo.
- [ ] Nếu live input đã được xử lý, dùng seed/input/group ID mới hoặc trình bày idempotent skip như expected behavior.
- [ ] Nếu MinIO/Airflow UI lỗi, dùng health output và screenshot dự phòng.
- [ ] Nếu connector lỗi, có `make cdc-status`, `make connect-logs` và `make cdc-restart` trong private operator notes.
- [ ] Nếu Airflow task lỗi, mở sanitized task log; không xóa component manifest để “làm xanh” demo.
- [ ] Demo rollback script/notes chỉ dừng đúng service và không xóa named volumes.

## 10. Sau demo

- [ ] Dừng recording/share screen trước khi thao tác credentials.
- [ ] Dừng services không cần thiết bằng scoped Make targets.
- [ ] Không xóa volumes nếu runtime data được giữ cho lần demo sau.
- [ ] Ghi lại seed, input checksum, consumer group, Airflow run IDs và known state cho rehearsal tiếp theo.
- [ ] Xóa screenshot/log export tạm nếu chứa dữ liệu confidential.
- [ ] Không commit generated `data/`, `tmp/`, `.env`, Airflow logs hoặc local manifests.
