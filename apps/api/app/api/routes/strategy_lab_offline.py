from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from app.schemas.strategy_lab_offline import (
    PatternIntelligenceRequest,
    PatternIntelligenceTradeRequest,
    StrategyLabCsvValidationRequest,
    StrategyLabCsvValidationResponse,
    StrategyLabDatasetCreateRequest,
    StrategyLabDatasetListResponse,
    StrategyLabDatasetResponse,
    StrategyLabReplayRequest,
)
from app.services.pattern_intelligence import analyze_selection, analyze_trade, analyze_visible_window
from app.services.strategy_lab_offline import analyze_csv, create_dataset, list_datasets, run_replay

router = APIRouter(prefix="/strategy-lab", tags=["offline-strategy-lab"])


@router.get("/datasets", response_model=StrategyLabDatasetListResponse)
async def datasets() -> StrategyLabDatasetListResponse:
    return StrategyLabDatasetListResponse(items=list_datasets())


@router.post("/datasets/validate", response_model=StrategyLabCsvValidationResponse)
async def validate_dataset(payload: StrategyLabCsvValidationRequest) -> StrategyLabCsvValidationResponse:
    report, _ = await run_in_threadpool(analyze_csv, payload.csv_text, payload.interval)
    return StrategyLabCsvValidationResponse.model_validate(report)


@router.post("/datasets", response_model=StrategyLabDatasetResponse, status_code=201)
async def upload_dataset(payload: StrategyLabDatasetCreateRequest) -> StrategyLabDatasetResponse:
    created = await run_in_threadpool(create_dataset, payload)
    return StrategyLabDatasetResponse.model_validate(created)


@router.post("/replay")
async def replay(payload: StrategyLabReplayRequest) -> dict[str, object]:
    return await run_in_threadpool(run_replay, payload)


@router.post("/pattern-intelligence/analyze-selection")
async def pattern_intelligence_selection(payload: PatternIntelligenceRequest) -> dict[str, object]:
    return await run_in_threadpool(analyze_selection, payload)


@router.post("/pattern-intelligence/analyze-visible-window")
async def pattern_intelligence_visible_window(payload: PatternIntelligenceRequest) -> dict[str, object]:
    return await run_in_threadpool(analyze_visible_window, payload)


@router.post("/pattern-intelligence/analyze-trade")
async def pattern_intelligence_trade(payload: PatternIntelligenceTradeRequest) -> dict[str, object]:
    return await run_in_threadpool(analyze_trade, payload)
