from __future__ import annotations

from prediction_market_bot.domain.enums import OutcomeClassification, PostmortemCause
from prediction_market_bot.domain.models import PostmortemReport, PredictionResult, ResearchPacket, SettlementResult


class PostmortemAgent:
    name = "postmortem-agent"

    def run(
        self,
        settled_trade: SettlementResult,
        prediction: PredictionResult,
        research: ResearchPacket,
    ) -> PostmortemReport:
        classification = self._classify_outcome(settled_trade)

        if classification == OutcomeClassification.WIN:
            return PostmortemReport(
                market_id=settled_trade.market_id,
                outcome_classification=classification,
                causes=tuple(),
                summary=(
                    f"Outcome={classification.value}. Positive pnl {settled_trade.pnl_usd:.2f} "
                    f"on market {settled_trade.market_id}."
                ),
                action_items=(
                    "Preserve this setup as a reference run.",
                    "Monitor if similar market regimes keep the same edge behavior.",
                ),
            )

        if classification in {OutcomeClassification.BREAKEVEN, OutcomeClassification.SKIPPED}:
            return PostmortemReport(
                market_id=settled_trade.market_id,
                outcome_classification=classification,
                causes=tuple(),
                summary=f"Outcome={classification.value}. No loss-driven postmortem needed.",
                action_items=("Continue monitoring and collect more evidence before changing parameters.",),
            )

        causes: list[PostmortemCause] = []
        action_items: list[str] = []

        if research.evidence_strength < 0.35:
            causes.append(PostmortemCause.DATA_GAP)
            action_items.append("Increase source coverage before approving trades.")
        if research.disagreement_score > 0.45:
            causes.append(PostmortemCause.RESEARCH_NOISE)
            action_items.append("Raise disagreement penalty in the research aggregation.")
        if prediction.confidence > 0.75:
            causes.append(PostmortemCause.CALIBRATION_ERROR)
            action_items.append("Review confidence scaling against realized outcomes.")
        if prediction.selected_market_price > 0.75:
            causes.append(PostmortemCause.LIQUIDITY_TRAP)
            action_items.append("Cap stake more aggressively on expensive outcomes.")

        if not causes:
            causes.append(PostmortemCause.RESOLUTION_MISREAD)
            action_items.append("Re-check settlement interpretation and source reliability.")

        summary = (
            f"Outcome={classification.value} on {settled_trade.market_id}: "
            f"pnl={settled_trade.pnl_usd:.2f}, confidence={prediction.confidence:.2f}, "
            f"evidence={research.evidence_strength:.2f}, disagreement={research.disagreement_score:.2f}."
        )
        return PostmortemReport(
            market_id=settled_trade.market_id,
            outcome_classification=classification,
            causes=tuple(causes),
            summary=summary,
            action_items=tuple(action_items),
        )

    @staticmethod
    def _classify_outcome(settled_trade: SettlementResult) -> OutcomeClassification:
        if settled_trade.outcome_classification != OutcomeClassification.BREAKEVEN:
            return settled_trade.outcome_classification
        if settled_trade.stake_usd <= 0.0:
            return OutcomeClassification.SKIPPED
        if settled_trade.pnl_usd > 0.0:
            return OutcomeClassification.WIN
        if settled_trade.pnl_usd < 0.0:
            return OutcomeClassification.LOSS
        return OutcomeClassification.BREAKEVEN
