from __future__ import annotations

from typing import Any


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _to_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _assess_ml_10x(
    sample: dict[str, Any] | None,
    *,
    effective_prob: float | None = None,
    btc_following: bool | None = None,
) -> dict[str, Any]:
    if not isinstance(sample, dict):
        return {
            'score': None,
            'ready': False,
            'reason': 'No pattern sample',
        }

    quality_tier = str(sample.get('quality_tier') or '').strip().upper()
    total_signals = _to_int(sample.get('total_signals'))
    win_rate_pct = _to_float(sample.get('win_rate_pct'))
    avg_mae_pct = _to_float(sample.get('avg_mae_pct'))
    avg_mfe_pct = _to_float(sample.get('avg_mfe_pct'))
    good_pattern = bool(sample.get('good_pattern'))
    match_strength = str(sample.get('match_strength') or '').strip().lower()
    effective_prob_value = _to_float(effective_prob)

    mae_abs = abs(avg_mae_pct) if avg_mae_pct < 0 else max(abs(avg_mae_pct), 0.05)
    rr_ratio = (avg_mfe_pct / mae_abs) if mae_abs > 0 else 0.0

    probability_score = _clamp((effective_prob_value - 0.72) / 0.14) * 25.0
    sample_score = _clamp(total_signals / 30.0) * 10.0
    win_rate_score = _clamp((win_rate_pct - 55.0) / 15.0) * 15.0
    quality_score = {
        'S': 12.0,
        'A': 12.0,
        'B': 9.0,
        'C': 4.0,
    }.get(quality_tier, 0.0)
    mae_score = _clamp((avg_mae_pct - (-4.0)) / 3.0) * 12.0
    mfe_score = _clamp((avg_mfe_pct - 1.5) / 3.0) * 8.0
    rr_score = _clamp((rr_ratio - 1.2) / 1.6) * 10.0
    pattern_score = 5.0 if good_pattern else 0.0
    btc_score = 3.0 if btc_following is True else 0.0
    match_score = {
        'exact': 5.0,
        'strong': 4.0,
        'partial': 2.0,
    }.get(match_strength, 0.0)

    score = round(min(100.0, max(0.0, probability_score + sample_score + win_rate_score + quality_score + mae_score + mfe_score + rr_score + pattern_score + btc_score + match_score)), 1)

    blockers: list[str] = []
    if effective_prob_value < 0.82:
        blockers.append('prob<82%')
    if total_signals < 20:
        blockers.append('sample<20')
    if win_rate_pct < 60.0:
        blockers.append('wr<60%')
    if avg_mae_pct < -2.5:
        blockers.append('mae<-2.5%')
    if avg_mfe_pct < 2.5:
        blockers.append('mfe<2.5%')
    if rr_ratio < 1.8:
        blockers.append('rr<1.8')
    if not good_pattern:
        blockers.append('pattern weak')
    if quality_tier not in {'S', 'A', 'B'}:
        blockers.append(f'tier {quality_tier or "-"}')

    ready = score >= 80.0 and not blockers

    reason_parts = [
        f'Prob {effective_prob_value * 100:.1f}%',
        f'Sample {total_signals}',
        f'WR {win_rate_pct:.1f}%',
        f'MAE {avg_mae_pct:+.2f}%',
        f'MFE {avg_mfe_pct:+.2f}%',
        f'RR {rr_ratio:.2f}',
    ]
    if btc_following is True:
        reason_parts.append('BTC follow')
    elif btc_following is False:
        reason_parts.append('BTC no-follow')
    if good_pattern:
        reason_parts.append('good pattern')
    if match_strength:
        reason_parts.append(f'match {match_strength}')
    if blockers:
        reason_parts.append('Need ' + ', '.join(blockers[:4]))
    else:
        reason_parts.append('Ready for 10x')

    return {
        'score': score,
        'ready': ready,
        'reason': ' | '.join(reason_parts),
    }


def assess_basic_ml_10x(
    sample: dict[str, Any] | None,
    *,
    effective_prob: float | None = None,
    btc_following: bool | None = None,
) -> dict[str, Any]:
    return _assess_ml_10x(
        sample,
        effective_prob=effective_prob,
        btc_following=btc_following,
    )


def assess_ml_candles_bg_10x(
    sample: dict[str, Any] | None,
    *,
    effective_prob: float | None = None,
    btc_following: bool | None = None,
) -> dict[str, Any]:
    return _assess_ml_10x(
        sample,
        effective_prob=effective_prob,
        btc_following=btc_following,
    )
