"""Bronze-to-Silver orchestration with lineage, idempotency, and safe publication."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from uuid import uuid4

import pyarrow as pa

from ingestion.batch.contracts import load_settlement_contract
from processing.silver.bronze_reader import BronzeReader, BronzeReadError
from processing.silver.cdc_normalizer import (
    SilverNormalizationError,
    history_row,
    normalize_cdc_table,
    project_entity_state,
)
from processing.silver.config import SilverSettings
from processing.silver.deduplication import deduplicate_events, event_order_key
from processing.silver.manifest import SqliteProcessingManifest
from processing.silver.models import (
    BUSINESS_KEYS,
    InputObject,
    NormalizedCdcEvent,
    OutputType,
    ProcessingResult,
    ProcessingStatus,
    QualityCode,
    QualityRejection,
    SilverOutput,
    SourceType,
    UnresolvedReference,
)
from processing.silver.parquet import SerializedSilver, serialize_rows
from processing.silver.quality import rejection
from processing.silver.references import classify_unresolved_references
from processing.silver.schemas import (
    HISTORY_SCHEMA,
    REJECTION_SCHEMA,
    SETTLEMENT_SCHEMA,
    UNRESOLVED_REFERENCE_SCHEMA,
    entity_schema,
)
from processing.silver.settlement_normalizer import normalize_settlement_bytes
from processing.silver.storage import SilverStorage, build_silver_object_key

CDC_PIPELINE = "cdc-bronze-to-silver"
SETTLEMENT_PIPELINE = "settlement-bronze-to-silver"


class SilverProcessor:
    def __init__(
        self,
        *,
        settings: SilverSettings,
        reader: BronzeReader,
        storage: SilverStorage,
        manifest: SqliteProcessingManifest,
        clock: Callable[[], datetime] | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.settings = settings
        self.reader = reader
        self.storage = storage
        self.manifest = manifest
        self.clock = clock or (lambda: datetime.now(UTC))
        self.run_id_factory = run_id_factory or (lambda: str(uuid4()))

    def process_cdc(
        self,
        input_object: InputObject,
        *,
        force_reprocess: bool = False,
        dry_run: bool = False,
    ) -> ProcessingResult:
        prior = self._existing(CDC_PIPELINE, input_object)
        entity = _entity_from_key(input_object.object_key)
        if prior is not None and not force_reprocess:
            return _skipped_result(prior)
        now = self.clock().astimezone(UTC)
        run_id = self.run_id_factory()
        if dry_run:
            read = self.reader.read_cdc(
                input_object,
                supported_schema_version=self.settings.supported_bronze_schema,
            )
            events, rejected = normalize_cdc_table(
                read.table,
                input_object=input_object,
                run_id=run_id,
                processed_at=now,
                silver_schema_version=self.settings.silver_schema_version,
            )
            accepted, duplicates = deduplicate_events(events)
            return ProcessingResult(
                run_id=None,
                input_object_uri=input_object.uri,
                status=ProcessingStatus.COMPLETED,
                entity_name=entity,
                input_record_count=read.table.num_rows,
                output_record_count=len(accepted),
                rejected_record_count=len(rejected) + len(duplicates),
                dry_run=True,
            )

        self.manifest.register(
            run_id=run_id,
            pipeline_name=CDC_PIPELINE,
            source_type=SourceType.CDC,
            entity_name=entity,
            input_object_uri=input_object.uri,
            input_checksum=input_object.checksum_sha256,
            code_version=self.settings.code_version,
            schema_version=self.settings.silver_schema_version,
            started_at=now,
        )
        try:
            self.manifest.mark_reading(run_id)
            read = self.reader.read_cdc(
                input_object,
                supported_schema_version=self.settings.supported_bronze_schema,
            )
            self.manifest.mark_validating(run_id, read.table.num_rows)
            events, rejections = normalize_cdc_table(
                read.table,
                input_object=input_object,
                run_id=run_id,
                processed_at=now,
                silver_schema_version=self.settings.silver_schema_version,
            )
            accepted, duplicates = deduplicate_events(events)
            for event, code in duplicates:
                rejections.append(
                    rejection(
                        source_object_uri=input_object.uri,
                        source_event_id=event.event_id,
                        entity_name=event.entity_name,
                        business_key=event.business_key,
                        code=code,
                        message="Duplicate CDC identity was excluded from Silver outputs",
                        raw_reference=f"coordinate:{event.kafka_topic}:{event.kafka_partition}:{event.kafka_offset}",
                        run_id=run_id,
                        rejected_at=now,
                    )
                )
            self.manifest.mark_transforming(run_id)
            outputs = self._transform_and_write_cdc(
                input_object=input_object,
                entity=entity,
                events=accepted,
                rejections=rejections,
                run_id=run_id,
                now=now,
                force_reprocess=force_reprocess,
            )
            output_count = sum(
                output.record_count
                for output in outputs
                if output.output_type is not OutputType.REJECTIONS
            )
            completed = self.manifest.mark_completed(
                run_id,
                outputs=outputs,
                output_record_count=output_count,
                rejected_record_count=len(rejections),
                completed_at=now,
            )
            return _result(completed)
        except BronzeReadError as error:
            current = self.manifest.get(run_id)
            if current is not None and current.status in {
                ProcessingStatus.READING,
                ProcessingStatus.VALIDATING,
            }:
                quarantined = self.manifest.mark_quarantined(
                    run_id,
                    error_code=error.code.value,
                    error_message=str(error),
                    rejected_record_count=1,
                    completed_at=now,
                )
                return _result(quarantined)
            raise
        except Exception as error:
            failed = self.manifest.mark_failed(run_id, error)
            return _result(failed)

    def process_settlement(
        self,
        input_object: InputObject,
        *,
        force_reprocess: bool = False,
        dry_run: bool = False,
    ) -> ProcessingResult:
        prior = self._existing(SETTLEMENT_PIPELINE, input_object)
        if prior is not None and not force_reprocess:
            return _skipped_result(prior)
        now = self.clock().astimezone(UTC)
        run_id = self.run_id_factory()
        if dry_run:
            payload = self.reader.read_bytes(input_object)
            contract = load_settlement_contract(self.settings.settlement_contract_path)
            rows, rejections, input_count, _partner_id = normalize_settlement_bytes(
                payload,
                input_object=input_object,
                contract=contract,
                run_id=run_id,
                processed_at=now,
                temp_dir=self.settings.temp_dir,
            )
            return ProcessingResult(
                run_id=None,
                input_object_uri=input_object.uri,
                status=ProcessingStatus.COMPLETED,
                entity_name="settlements",
                input_record_count=input_count,
                output_record_count=len(rows),
                rejected_record_count=len(rejections),
                dry_run=True,
            )
        self.manifest.register(
            run_id=run_id,
            pipeline_name=SETTLEMENT_PIPELINE,
            source_type=SourceType.SETTLEMENT,
            entity_name="settlements",
            input_object_uri=input_object.uri,
            input_checksum=input_object.checksum_sha256,
            code_version=self.settings.code_version,
            schema_version=self.settings.silver_schema_version,
            started_at=now,
        )
        try:
            self.manifest.mark_reading(run_id)
            payload = self.reader.read_bytes(input_object)
            contract = load_settlement_contract(self.settings.settlement_contract_path)
            rows, rejections, input_count, partner_id = normalize_settlement_bytes(
                payload,
                input_object=input_object,
                contract=contract,
                run_id=run_id,
                processed_at=now,
                temp_dir=self.settings.temp_dir,
            )
            self.manifest.mark_validating(run_id, input_count)
            self.manifest.mark_transforming(run_id)
            self.manifest.mark_writing(run_id)
            outputs: list[SilverOutput] = []
            settlement_date = (
                rows[0]["settlement_date"]
                if rows
                else _date_from_key(input_object.object_key, "settlement_date", now.date())
            )
            if rows:
                outputs.append(
                    self._write_rows(
                        rows,
                        SETTLEMENT_SCHEMA,
                        output_type=OutputType.SETTLEMENTS,
                        entity="settlements",
                        run_id=run_id,
                        now=now,
                        input_object=input_object,
                        partner_id=partner_id,
                        settlement_date=settlement_date,  # type: ignore[arg-type]
                    )
                )
            if rejections:
                outputs.append(
                    self._write_rejections(
                        rejections,
                        input_object=input_object,
                        entity="settlements",
                        run_id=run_id,
                        now=now,
                    )
                )
            completed = self.manifest.mark_completed(
                run_id,
                outputs=tuple(outputs),
                output_record_count=len(rows),
                rejected_record_count=len(rejections),
                completed_at=now,
            )
            return _result(completed)
        except Exception as error:
            return _result(self.manifest.mark_failed(run_id, error))

    def _transform_and_write_cdc(
        self,
        *,
        input_object: InputObject,
        entity: str,
        events: list[NormalizedCdcEvent],
        rejections: list[QualityRejection],
        run_id: str,
        now: datetime,
        force_reprocess: bool,
    ) -> tuple[SilverOutput, ...]:
        history = [history_row(event) for event in events]
        prior_state = self._load_latest_state(
            entity,
            exclude_input_checksum=(input_object.checksum_sha256 if force_reprocess else None),
        )
        state = {str(row[BUSINESS_KEYS[entity]]): row for row in prior_state}
        event_rows: list[dict[str, object]] = []
        unresolved: list[UnresolvedReference] = []
        known_keys = self._known_reference_keys()
        for event in sorted(events, key=event_order_key):
            prior = state.get(event.business_key)
            if prior is not None:
                prior_topic = str(prior.get("kafka_topic", ""))
                prior_partition = int(prior.get("kafka_partition", -1))
                prior_offset = int(prior.get("kafka_offset", -1))
                if prior_topic == event.kafka_topic and prior_partition == event.kafka_partition:
                    if event.kafka_offset <= prior_offset:
                        if event.kafka_offset < prior_offset:
                            rejections.append(
                                self._event_rejection(
                                    input_object,
                                    event,
                                    QualityCode.OUT_OF_ORDER_EVENT,
                                    "CDC event precedes the published entity state",
                                    now,
                                )
                            )
                        continue
                elif prior_topic == event.kafka_topic:
                    rejections.append(
                        self._event_rejection(
                            input_object,
                            event,
                            QualityCode.OUT_OF_ORDER_EVENT,
                            "Business key moved across Kafka partitions",
                            now,
                        )
                    )
                    continue
            try:
                if event.is_tombstone:
                    if prior is None:
                        continue
                    row = dict(prior)
                    _apply_event_lineage(row, event, is_deleted=True)
                else:
                    row = project_entity_state(event)
            except SilverNormalizationError as error:
                rejections.append(
                    self._event_rejection(input_object, event, error.code, str(error), now)
                )
                continue
            if entity == "transaction_events":
                event_rows.append(row)
            else:
                state[event.business_key] = row
                known_keys.setdefault(entity, set()).add(event.business_key)
                unresolved.extend(
                    classify_unresolved_references(event, row, known_keys, observed_at=now)
                )

        self.manifest.mark_writing(run_id)
        outputs: list[SilverOutput] = []
        event_date = events[0].event_time.date() if events else now.date()
        if history:
            outputs.append(
                self._write_rows(
                    history,
                    HISTORY_SCHEMA,
                    output_type=OutputType.HISTORY,
                    entity=entity,
                    run_id=run_id,
                    now=now,
                    input_object=input_object,
                    event_date=event_date,
                )
            )
        if entity == "transaction_events":
            if event_rows:
                outputs.append(
                    self._write_rows(
                        event_rows,
                        entity_schema(entity),
                        output_type=OutputType.EVENTS,
                        entity=entity,
                        run_id=run_id,
                        now=now,
                        input_object=input_object,
                        event_date=event_date,
                    )
                )
        else:
            latest_rows = [state[key] for key in sorted(state)]
            current_rows = [row for row in latest_rows if not bool(row["is_deleted"])]
            outputs.append(
                self._write_rows(
                    latest_rows,
                    entity_schema(entity),
                    output_type=OutputType.LATEST_ALL,
                    entity=entity,
                    run_id=run_id,
                    now=now,
                    input_object=input_object,
                )
            )
            outputs.append(
                self._write_rows(
                    current_rows,
                    entity_schema(entity),
                    output_type=OutputType.CURRENT,
                    entity=entity,
                    run_id=run_id,
                    now=now,
                    input_object=input_object,
                )
            )
        if rejections:
            outputs.append(
                self._write_rejections(
                    rejections, input_object=input_object, entity=entity, run_id=run_id, now=now
                )
            )
        if unresolved:
            outputs.append(
                self._write_rows(
                    [vars_from_slots(item) for item in unresolved],
                    UNRESOLVED_REFERENCE_SCHEMA,
                    output_type=OutputType.UNRESOLVED_REFERENCES,
                    entity=entity,
                    run_id=run_id,
                    now=now,
                    input_object=input_object,
                )
            )
        return tuple(outputs)

    def _write_rejections(
        self,
        rows: list[QualityRejection],
        *,
        input_object: InputObject,
        entity: str,
        run_id: str,
        now: datetime,
    ) -> SilverOutput:
        return self._write_rows(
            [vars_from_slots(item) for item in rows],
            REJECTION_SCHEMA,
            output_type=OutputType.REJECTIONS,
            entity=f"bronze:{entity}",
            run_id=run_id,
            now=now,
            input_object=input_object,
        )

    def _write_rows(
        self,
        rows: list[dict[str, object]],
        schema: pa.Schema,
        *,
        output_type: OutputType,
        entity: str,
        run_id: str,
        now: datetime,
        input_object: InputObject,
        event_date: date | None = None,
        partner_id: str | None = None,
        settlement_date: date | None = None,
    ) -> SilverOutput:
        serialized: SerializedSilver | None = None
        storage_entity = entity.split(":", 1)[-1]
        try:
            serialized = serialize_rows(
                rows,
                schema=schema,
                temp_dir=self.settings.temp_dir,
                prefix=f"{run_id}-{output_type.value}",
            )
            key = build_silver_object_key(
                output_type=output_type,
                entity_name=entity,
                run_id=run_id,
                processing_date=now.date(),
                event_date=event_date,
                partner_id=partner_id,
                settlement_date=settlement_date,
            )
            return self.storage.put(
                serialized=serialized,
                object_key=key,
                output_type=output_type,
                entity_name=storage_entity,
                run_id=run_id,
                input_checksum=input_object.checksum_sha256,
                code_version=self.settings.code_version,
                source_schema_version=(
                    self.settings.supported_bronze_schema
                    if output_type is not OutputType.SETTLEMENTS
                    else "settlement-v1"
                ),
                silver_schema_version=self.settings.silver_schema_version,
                processed_at=now,
            )
        finally:
            if serialized is not None:
                serialized.path.unlink(missing_ok=True)

    def _load_latest_state(
        self, entity: str, *, exclude_input_checksum: str | None = None
    ) -> list[dict[str, object]]:
        output = self.manifest.latest_output(
            entity,
            OutputType.LATEST_ALL,
            exclude_input_checksum=exclude_input_checksum,
        )
        if output is None:
            return []
        return self.storage.read_table(output.object_uri, entity_schema(entity)).to_pylist()

    def _known_reference_keys(self) -> dict[str, set[str]]:
        known: dict[str, set[str]] = {}
        for entity, key_name in BUSINESS_KEYS.items():
            if entity == "transaction_events":
                continue
            output = self.manifest.latest_output(entity, OutputType.CURRENT)
            if output is None:
                continue
            rows = self.storage.read_table(output.object_uri, entity_schema(entity)).to_pylist()
            known[entity] = {str(row[key_name]) for row in rows if row.get(key_name)}
        return known

    def _existing(self, pipeline: str, input_object: InputObject):
        return self.manifest.find_latest_identity(
            pipeline_name=pipeline,
            input_checksum=input_object.checksum_sha256,
            code_version=self.settings.code_version,
            schema_version=self.settings.silver_schema_version,
        )

    @staticmethod
    def _event_rejection(
        input_object: InputObject,
        event: NormalizedCdcEvent,
        code: QualityCode,
        message: str,
        now: datetime,
    ) -> QualityRejection:
        return rejection(
            source_object_uri=input_object.uri,
            source_event_id=event.event_id,
            entity_name=event.entity_name,
            business_key=event.business_key,
            code=code,
            message=message,
            raw_reference=f"coordinate:{event.kafka_topic}:{event.kafka_partition}:{event.kafka_offset}",
            run_id=event.processing_run_id,
            rejected_at=now,
        )


def _apply_event_lineage(
    row: dict[str, object], event: NormalizedCdcEvent, *, is_deleted: bool
) -> None:
    row.update(
        {
            "is_deleted": is_deleted,
            "source_lsn": event.source_lsn,
            "kafka_topic": event.kafka_topic,
            "kafka_partition": event.kafka_partition,
            "kafka_offset": event.kafka_offset,
            "effective_event_time": event.event_time,
            "processed_at": event.processed_at,
            "processing_run_id": event.processing_run_id,
            "source_schema_version": event.source_schema_version,
            "silver_schema_version": event.silver_schema_version,
        }
    )


def vars_from_slots(value: object) -> dict[str, object]:
    return {name: getattr(value, name) for name in value.__dataclass_fields__}  # type: ignore[attr-defined]


def _entity_from_key(object_key: str) -> str:
    for part in object_key.split("/"):
        if part.startswith("entity="):
            return part.removeprefix("entity=")
    return "unknown"


def _date_from_key(object_key: str, name: str, default: date) -> date:
    prefix = f"{name}="
    for part in object_key.split("/"):
        if part.startswith(prefix):
            try:
                return date.fromisoformat(part.removeprefix(prefix))
            except ValueError:
                return default
    return default


def _result(run) -> ProcessingResult:
    return ProcessingResult(
        run_id=run.run_id,
        input_object_uri=run.input_object_uri,
        status=run.status,
        entity_name=run.entity_name,
        input_record_count=run.input_record_count,
        output_record_count=run.output_record_count,
        rejected_record_count=run.rejected_record_count,
        output_object_uris=run.output_object_uris,
        error_code=run.error_code,
        error_message=run.error_message,
    )


def _skipped_result(run) -> ProcessingResult:
    result = _result(run)
    return ProcessingResult(
        run_id=result.run_id,
        input_object_uri=result.input_object_uri,
        status=result.status,
        entity_name=result.entity_name,
        input_record_count=result.input_record_count,
        output_record_count=result.output_record_count,
        rejected_record_count=result.rejected_record_count,
        output_object_uris=result.output_object_uris,
        skipped=True,
    )
