from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class StrategyLabParameters(BaseModel):
    entry_offset_pct: Decimal = Field(default=Decimal("0.01"), ge=0, lt=1)
    initial_stop_pct: Decimal = Field(default=Decimal("0.01"), ge=0)
    profit_activation_pct: Decimal = Field(default=Decimal("0.03"), ge=0)
    trailing_distance_pct: Decimal = Field(default=Decimal("0.01"), ge=0)
    required_declining_candles: int = Field(default=2, ge=2, le=20)
    fee_pct: Decimal = Field(default=Decimal("0.002"), ge=0)
    slippage_pct: Decimal = Field(default=Decimal("0.0005"), ge=0)
    initial_capital: Decimal = Field(default=Decimal("100"), gt=0)
    trade_deployment_pct: Decimal = Field(default=Decimal("100"), gt=0, le=100)
    profit_compound_pct: Decimal = Field(default=Decimal("100"), ge=0, le=100)
    profit_withdrawal_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    profit_tax_reserve_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100)

    @model_validator(mode="after")
    def validate_profit_allocation(self) -> "StrategyLabParameters":
        total = self.profit_compound_pct + self.profit_withdrawal_pct + self.profit_tax_reserve_pct
        if total != Decimal("100"):
            raise ValueError("compounding, withdrawal, and tax reserve percentages must sum to 100")
        return self


class StrategyLabReplayRequest(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=120)
    strategy_version: Literal["001", "002"] = "002"
    start_time: datetime | None = None
    end_time: datetime | None = None
    research_period: Literal["training", "validation", "out_of_sample", "entire_dataset"] = "training"
    parameters: StrategyLabParameters = Field(default_factory=StrategyLabParameters)


class StrategyLabCsvValidationRequest(BaseModel):
    csv_text: str = Field(min_length=1, max_length=50_000_000)
    interval: str | None = Field(default=None, max_length=20)


class StrategyLabDatasetCreateRequest(BaseModel):
    csv_text: str = Field(min_length=1, max_length=50_000_000)
    asset: str = Field(min_length=1, max_length=40)
    exchange: str = Field(min_length=1, max_length=80)
    interval: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)


class StrategyLabDatasetResponse(BaseModel):
    id: str
    name: str
    asset: str
    exchange: str
    interval: str
    candle_count: int
    first_timestamp: datetime
    last_timestamp: datetime
    missing_candles: int = 0
    duplicate_timestamps: int = 0
    invalid_rows: int = 0


class StrategyLabCsvValidationResponse(BaseModel):
    valid: bool
    required_columns: list[str]
    missing_columns: list[str]
    total_rows: int
    candle_count: int
    first_timestamp: datetime | None
    last_timestamp: datetime | None
    missing_candles: int
    duplicate_timestamps: int
    invalid_rows: int
    errors: list[str]


class StrategyLabDatasetListResponse(BaseModel):
    items: list[StrategyLabDatasetResponse]


class PatternIntelligenceRequest(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=120)
    strategy_version: Literal["001", "002"] = "002"
    selected_start_index: int = Field(default=0, ge=0)
    selected_end_index: int | None = Field(default=None, ge=0)
    partition: Literal["training", "validation", "final_test", "entire_dataset"] = "entire_dataset"
    parameters: StrategyLabParameters = Field(default_factory=StrategyLabParameters)


class PatternIntelligenceTradeRequest(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=120)
    strategy_version: Literal["001", "002"] = "002"
    trade_index: int = Field(ge=0)
    partition: Literal["training", "validation", "final_test", "entire_dataset"] = "entire_dataset"
    parameters: StrategyLabParameters = Field(default_factory=StrategyLabParameters)


class ResearchCopilotRequest(PatternIntelligenceRequest):
    final_test_used_for_development: bool = False
    hypotheses_tested_on_partition: int = Field(default=0, ge=0, le=100_000)
    sensitivity_results: list[dict[str, str | int | float | bool | None]] = Field(default_factory=list, max_length=100)


class ResearchCopilotTradeRequest(PatternIntelligenceTradeRequest):
    final_test_used_for_development: bool = False
    hypotheses_tested_on_partition: int = Field(default=0, ge=0, le=100_000)
    sensitivity_results: list[dict[str, str | int | float | bool | None]] = Field(default_factory=list, max_length=100)


class CandidateRuleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=2000)
    source_analysis_id: str = Field(min_length=1, max_length=160)
    source_finding_ids: list[str] = Field(min_length=1, max_length=100)
    source_candidate_experiment_id: str = Field(min_length=1, max_length=80)
    parent_strategy_version: Literal["001", "002"] = "002"
    rule_document: dict
    created_by: Literal["human", "human_with_copilot"] = "human_with_copilot"
    research_notes: str = Field(default="", max_length=10000)
    evidence: dict = Field(default_factory=dict)


class RuleDocumentValidationRequest(BaseModel):
    rule_document: dict


class CandidateRuleUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=2000)
    rule_document: dict
    research_notes: str = Field(default="", max_length=10000)


class BranchReplayRequest(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=120)
    partition: Literal["training", "validation", "final_test", "entire_dataset"]
    parameters: StrategyLabParameters = Field(default_factory=StrategyLabParameters)
