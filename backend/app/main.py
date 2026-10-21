"""FastAPI application: sessions, audio ingest, event stream, and component health."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from app.alerts.channels import DashboardChannel
from app.alerts.dispatcher import CallAlerts
from app.analysis import CallAnalyzer
from app.audio.buffer import FRAME_BYTES
from app.audio.stt import FasterWhisperProvider, STTProvider
from app.audio.vad import VadConfig, VadSegmenter, WebRtcVad
from app.coercion.engine import CoercionEngine
from app.coercion.model_tagger import ModelTagger, ModelUnavailable, TacticModel
from app.components import ComponentRegistry
from app.config import Settings, get_settings
from app.entities.extract import EntityExtractor
from app.events import ComponentStatus, EventBus
from app.pipeline import CallPipeline
from app.session import SessionManager
from app.storage import Storage
from app.verify.institutions import InstitutionOut, default_directory
from app.verify.manual import (
    ManualCheckRequest,
    ManualCheckResponse,
    UnknownInstitution,
    run_manual_check,
)
from app.verify.tinyfish_client import TinyFishClient, TinyFishUnavailable
from app.verify.verifier import Verifier
from app.voice.antispoof import UnavailableAntiSpoof

log = logging.getLogger("scamshield")

VadClassifier = Callable[[bytes], bool]


def _init_component_status(settings: Settings, components: ComponentRegistry) -> None:
    if settings.llm_enabled:
        components.set("llm", ComponentStatus.UNAVAILABLE, "LLM tagger not implemented yet")
    else:
        components.set("llm", ComponentStatus.DISABLED, "LLM_ENABLED=false")
    components.set("voice", *UnavailableAntiSpoof().status())
    components.set("alerts", ComponentStatus.OK, "dashboard channel (SMS/WhatsApp in B9)")


def _load_coercion_model(settings: Settings, components: ComponentRegistry) -> ModelTagger | None:
    """Load and warm up the trained tactic classifier; report why when it can't run."""
    if not settings.coercion_model_enabled:
        components.set(
            "classifier",
            ComponentStatus.DISABLED,
            "COERCION_MODEL_ENABLED=false; coercion runs on the lexicon fallback",
        )
        return None
    try:
        model = TacticModel.load(
            settings.resolved_coercion_model_dir, settings.coercion_model_threads
        )
        tagger = ModelTagger.from_config(model)
        tagger.hits("warm up")  # the first ONNX run allocates; keep it off the first call
        components.set("classifier", ComponentStatus.OK, tagger.describe())
        return tagger
    except ModelUnavailable as exc:
        components.set(
            "classifier",
            ComponentStatus.UNAVAILABLE,
            f"{exc}; run `make train-coercion`; coercion runs on the lexicon fallback",
        )
    except Exception as exc:
        log.exception("coercion classifier failed to load")
        components.set(
            "classifier",
            ComponentStatus.UNAVAILABLE,
            f"load failed: {exc}; coercion runs on the lexicon fallback",
        )
    return None


def create_app(
    settings: Settings | None = None,
    stt: STTProvider | None = None,
    vad_classifier: VadClassifier | None = None,
    tinyfish_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Build the app. `stt` and `vad_classifier` can be injected (tests); defaults are real models."""
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        components = ComponentRegistry()
        storage = Storage(settings.resolved_db_path, settings.store_transcripts)
        try:
            await storage.open()
            components.set("storage", ComponentStatus.OK, str(settings.resolved_db_path))
        except Exception as exc:
            log.exception("storage failed to open")
            components.set("storage", ComponentStatus.UNAVAILABLE, f"sqlite open failed: {exc}")
        _init_component_status(settings, components)

        classifier = vad_classifier
        if classifier is None:
            try:
                classifier = WebRtcVad(settings.vad_aggressiveness)
                components.set(
                    "vad",
                    ComponentStatus.OK,
                    f"webrtcvad aggressiveness={settings.vad_aggressiveness}",
                )
            except Exception as exc:
                log.exception("VAD failed to initialise")
                components.set("vad", ComponentStatus.UNAVAILABLE, f"webrtcvad init failed: {exc}")
        else:
            components.set("vad", ComponentStatus.OK, "injected classifier")

        stt_provider = stt or FasterWhisperProvider.from_settings(settings)

        async def load_stt() -> None:
            components.set("stt", ComponentStatus.LOADING, f"loading {stt_provider.name}")
            await stt_provider.load()
            components.set("stt", *stt_provider.status())

        stt_task = None
        if settings.stt_load_on_startup:
            stt_task = asyncio.create_task(load_stt(), name="stt-load")
        else:
            components.set("stt", *stt_provider.status())

        vad_config = VadConfig.from_settings(settings)

        components.set("classifier", ComponentStatus.LOADING, "loading tactic classifier")
        model_tagger = await asyncio.to_thread(_load_coercion_model, settings, components)
        engine: CoercionEngine | None = None
        extractor: EntityExtractor | None = None
        try:
            engine = CoercionEngine.load(
                model=model_tagger, model_timeout_s=settings.coercion_model_timeout_s
            )
            extractor = EntityExtractor.load(engine.lexicon.cfg)
            components.set("lexicon", ComponentStatus.OK, engine.describe())
        except Exception as exc:
            log.exception("coercion lexicon failed to load")
            components.set("lexicon", ComponentStatus.UNAVAILABLE, f"load failed: {exc}")

        def make_pipeline(session_id: str, bus: EventBus) -> CallPipeline | None:
            if classifier is None:
                return None
            return CallPipeline(
                session_id,
                bus,
                settings,
                stt_provider,
                VadSegmenter(vad_config, classifier),
                components,
                analyzer=CallAnalyzer(engine, extractor) if engine and extractor else None,
                alerts=CallAlerts(
                    session_id,
                    [DashboardChannel(bus)],
                    storage,
                    settings.elder_name,
                    settings.alert_send_timeout_s,
                ),
            )

        key = settings.tinyfish_api_key.get_secret_value() if settings.tinyfish_api_key else None
        tinyfish = TinyFishClient(
            key,
            timeout_s=settings.tinyfish_timeout_s,
            search_per_minute=settings.tinyfish_search_per_minute,
            fetch_per_minute=settings.tinyfish_fetch_per_minute,
            location=settings.tinyfish_location,
            transport=tinyfish_transport,
        )
        verifier = Verifier(tinyfish, storage, settings.verify_cache_ttl_s)
        components.set("verify", *verifier.status())

        async def probe_tinyfish() -> None:
            # One documented Search call so /health reports TinyFish's real state, not a guess.
            with suppress(TinyFishUnavailable):
                await tinyfish.search("Reserve Bank of India", include_domains=["rbi.org.in"])
            components.set("verify", *verifier.status())

        probe_task = (
            asyncio.create_task(probe_tinyfish(), name="tinyfish-probe")
            if tinyfish.configured
            else None
        )

        sessions = SessionManager(settings, storage, components, pipeline_factory=make_pipeline)
        await sessions.start()
        app.state.settings = settings
        app.state.components = components
        app.state.storage = storage
        app.state.sessions = sessions
        app.state.stt = stt_provider
        app.state.stt_loading = stt_task is not None
        app.state.engine = engine
        app.state.extractor = extractor
        app.state.verifier = verifier
        try:
            yield
        finally:
            await sessions.stop()
            if probe_task is not None:
                probe_task.cancel()
            await tinyfish.close()
            if model_tagger is not None:
                model_tagger.close()
            if stt_task is not None:
                stt_task.cancel()
            close = getattr(stt_provider, "close", None)
            if close is not None:
                close()
            await storage.close()

    app = FastAPI(title="Scam Shield", version="0.2.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        components: ComponentRegistry = app.state.components
        return {
            "status": components.overall(),
            "active_sessions": len(app.state.sessions),
            "components": {
                name: {
                    "status": state.status.value,
                    "detail": state.detail,
                    "since": state.since.isoformat(),
                }
                for name, state in components.snapshot().items()
            },
        }

    @app.get("/verify/institutions", response_model=list[InstitutionOut])
    async def verify_institutions() -> list[InstitutionOut]:
        """The trusted directory, for the /check dropdown (English + Hindi names)."""
        return [InstitutionOut.of(i) for i in default_directory().institutions]

    @app.post("/verify/manual", response_model=ManualCheckResponse)
    async def verify_manual(req: ManualCheckRequest) -> ManualCheckResponse:
        if app.state.engine is None or app.state.extractor is None:
            raise HTTPException(status_code=503, detail="coercion engine unavailable")
        verifier: Verifier = app.state.verifier
        try:
            result = await run_manual_check(
                req,
                app.state.engine,
                app.state.extractor,
                verifier if verifier.client.configured else None,
                settings.verify_budget_s,
            )
        except UnknownInstitution as exc:
            raise HTTPException(status_code=422, detail=f"unknown institution_id {exc}") from exc
        app.state.components.set("verify", *verifier.status())
        return result

    @app.post("/sessions", status_code=201)
    async def create_session() -> dict:
        session = await app.state.sessions.create()
        return {"session_id": session.id}

    @app.post("/sessions/{session_id}/end")
    async def end_session(session_id: str) -> dict:
        if not await app.state.sessions.end(session_id, reason="ended by client"):
            raise HTTPException(status_code=404, detail="unknown or already ended session")
        return {"session_id": session_id, "ended": True}

    @app.websocket("/ws/audio/{session_id}")
    async def audio_ws(websocket: WebSocket, session_id: str) -> None:
        session = app.state.sessions.get(session_id)
        if session is None:
            await websocket.close(code=4404, reason="unknown session")
            return
        if session.pipeline is None:
            await websocket.close(code=4503, reason="audio pipeline unavailable (VAD)")
            return
        if session.audio_connected:
            await websocket.close(code=4409, reason="audio already connected for this session")
            return
        # Don't take audio until the STT model is warm, so the first utterance isn't lost.
        if app.state.stt_loading and not await app.state.stt.wait_ready(settings.stt_ready_wait_s):
            await websocket.close(code=4503, reason="speech-to-text still loading, retry")
            return
        await websocket.accept()
        session.audio_connected = True
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if data is None:
                    await websocket.close(code=1003, reason="send binary PCM frames only")
                    break
                if session.ended:
                    await websocket.close(code=1000, reason="session ended")
                    break
                try:
                    session.pipeline.feed(data)
                except ValueError:
                    await websocket.close(
                        code=1003,
                        reason=f"frames must be 20 ms of 16 kHz mono int16 ({FRAME_BYTES} bytes)",
                    )
                    break
                session.touch()
        except WebSocketDisconnect:
            pass
        finally:
            session.audio_connected = False
            if not session.ended:
                session.pipeline.flush()

    @app.websocket("/ws/events/{session_id}")
    async def events_ws(websocket: WebSocket, session_id: str) -> None:
        session = app.state.sessions.get(session_id)
        if session is None:
            await websocket.close(code=4404, reason="unknown session")
            return
        await websocket.accept()
        sub = session.bus.subscribe(replay=True)
        try:
            async for event in sub:
                await websocket.send_text(event.model_dump_json())
            await websocket.close(code=1000, reason="session ended")
        except WebSocketDisconnect:
            pass
        finally:
            sub.close()

    return app


app = create_app()
