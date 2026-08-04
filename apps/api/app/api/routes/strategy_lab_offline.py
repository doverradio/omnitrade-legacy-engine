from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from app.schemas.strategy_lab_offline import (
    BranchReplayRequest,
    CandidateRuleCreateRequest,
    CandidateRuleUpdateRequest,
    PatternIntelligenceRequest,
    PatternIntelligenceTradeRequest,
    ResearchCopilotRequest,
    ResearchCopilotTradeRequest,
    RuleDocumentValidationRequest,
    StrategyLabCsvValidationRequest,
    StrategyLabCsvValidationResponse,
    StrategyLabDatasetCreateRequest,
    StrategyLabDatasetListResponse,
    StrategyLabDatasetResponse,
    StrategyLabReplayRequest,
)
from app.services.rule_discovery import (
    branch_comparison, branch_package, create_branch, create_rule, get_rule, list_rules,
    replay_branch, update_rule, validate_rule, validate_rule_document_request,
)
from app.services.pattern_intelligence import analyze_selection, analyze_trade, analyze_visible_window
from app.services.research_copilot import explain_selection, explain_success, explain_trade, overfitting_warnings, show_missed
from app.services.strategy_lab_offline import analyze_csv, create_dataset, list_datasets, run_replay

router = APIRouter(prefix="/strategy-lab", tags=["offline-strategy-lab"])
rule_discovery_router = APIRouter(prefix="/api/v1/strategy-lab", tags=["offline-rule-discovery"])


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


@router.post("/research-copilot/explain-selection")
async def research_copilot_selection(payload: ResearchCopilotRequest) -> dict[str, object]:
    return await run_in_threadpool(explain_selection, payload)


@router.post("/research-copilot/show-missed")
async def research_copilot_missed(payload: ResearchCopilotRequest) -> dict[str, object]:
    return await run_in_threadpool(show_missed, payload)


@router.post("/research-copilot/explain-trade")
async def research_copilot_trade(payload: ResearchCopilotTradeRequest) -> dict[str, object]:
    return await run_in_threadpool(explain_trade, payload)


@router.post("/research-copilot/explain-success")
async def research_copilot_success(payload: ResearchCopilotTradeRequest) -> dict[str, object]:
    return await run_in_threadpool(explain_success, payload)


@router.post("/research-copilot/overfitting-warnings")
async def research_copilot_overfitting(payload: ResearchCopilotRequest) -> dict[str, object]:
    return await run_in_threadpool(overfitting_warnings, payload)


@rule_discovery_router.post("/rules", status_code=201)
async def rule_create(payload: CandidateRuleCreateRequest) -> dict[str, object]:
    return await run_in_threadpool(create_rule, payload)


@rule_discovery_router.post("/rules/validate-document")
async def rule_validate_document(payload: RuleDocumentValidationRequest) -> dict[str, object]:
    return await run_in_threadpool(validate_rule_document_request, payload.rule_document)


@rule_discovery_router.get("/rules")
async def rule_list() -> dict[str, object]:
    return {"items": await run_in_threadpool(list_rules)}


@rule_discovery_router.get("/rules/{rule_id}")
async def rule_get(rule_id: str) -> dict[str, object]:
    return await run_in_threadpool(get_rule, rule_id)


@rule_discovery_router.put("/rules/{rule_id}")
async def rule_update(rule_id: str, payload: CandidateRuleUpdateRequest) -> dict[str, object]:
    return await run_in_threadpool(update_rule, rule_id, payload)


@rule_discovery_router.post("/rules/{rule_id}/validate")
async def rule_validate(rule_id: str) -> dict[str, object]:
    return await run_in_threadpool(validate_rule, rule_id)


@rule_discovery_router.post("/rules/{rule_id}/create-branch")
async def rule_create_branch(rule_id: str) -> dict[str, object]:
    return await run_in_threadpool(create_branch, rule_id)


@rule_discovery_router.post("/branches/{branch_id}/replay")
async def branch_replay(branch_id: str, payload: BranchReplayRequest) -> dict[str, object]:
    return await run_in_threadpool(replay_branch, branch_id, payload)


@rule_discovery_router.get("/branches/{branch_id}/comparison")
async def branch_compare(branch_id: str, dataset_id: str) -> dict[str, object]:
    return await run_in_threadpool(branch_comparison, branch_id, dataset_id)


@rule_discovery_router.get("/branches/{branch_id}/package")
async def branch_export_package(branch_id: str, dataset_id: str) -> dict[str, object]:
    return await run_in_threadpool(branch_package, branch_id, dataset_id)
