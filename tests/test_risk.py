from app.strategy.risk_rules import RiskEngine, RiskSnapshot


def test_risk_blocks_daily_loss():
    engine = RiskEngine(daily_max_loss=10, max_consecutive_losses=3, max_positions=1)
    ok, reason = engine.can_enter(RiskSnapshot(realized_pnl=-11))
    assert not ok
    assert "Daily" in reason
