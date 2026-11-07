"""Pricing rules. Today: the transit fee charged online on cash-on-delivery orders."""

from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from .models import CodTransitPolicy

CENT = Decimal("0.01")


def _applies(policy: CodTransitPolicy, subtotal: Decimal, state: str, pincode: str):
    """Return a specificity rank if the policy applies to this order, else None."""
    if policy.min_order_value is not None and subtotal < policy.min_order_value:
        return None
    if policy.max_order_value is not None and subtotal > policy.max_order_value:
        return None
    if policy.scope == CodTransitPolicy.Scope.DEFAULT:
        return (0, 0)
    if policy.scope == CodTransitPolicy.Scope.STATE:
        return (1, 0) if policy.match.casefold() == state.strip().casefold() else None
    return (
        (2, len(policy.match)) if pincode.startswith(policy.match) else None
    )  # longer prefix = more specific


def quote_cod_fee(subtotal: Decimal, *, state: str, pincode: str, at=None) -> tuple[Decimal, dict]:
    """The COD transit fee for an order, plus a snapshot of the rule that produced it.

    The most specific active rule wins (pincode prefix > state > default); among equals the newest. No
    matching rule means no fee. The snapshot is stored with the order, so editing a rule later never
    changes an order that was already priced.
    """
    now = at or timezone.now()
    ranked = []
    for policy in CodTransitPolicy.objects.filter(is_active=True, effective_from__lte=now):
        rank = _applies(policy, subtotal, state, pincode)
        if rank is not None:
            ranked.append((rank, policy.effective_from, policy))
    if not ranked:
        return Decimal(0), {}
    policy = max(ranked, key=lambda r: (r[0], r[1]))[2]

    fee = policy.value if policy.mode == CodTransitPolicy.Mode.FIXED else subtotal * policy.value / 100
    if policy.min_fee is not None:
        fee = max(fee, policy.min_fee)
    if policy.max_fee is not None:
        fee = min(fee, policy.max_fee)
    snapshot = {
        "policy_id": str(policy.pk),
        "scope": policy.scope,
        "match": policy.match,
        "mode": policy.mode,
        "value": str(policy.value),
        "min_fee": str(policy.min_fee) if policy.min_fee is not None else None,
        "max_fee": str(policy.max_fee) if policy.max_fee is not None else None,
    }
    return fee.quantize(CENT, rounding=ROUND_HALF_UP), snapshot
