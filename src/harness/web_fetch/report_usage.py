"""Native model usage from immutable per-result receipts."""

from collections import defaultdict


def token_usage(records):
    """Keep cache within input, and never turn partial telemetry into a total."""
    groups = defaultdict(list)
    for record in records:
        groups[record['provider']].append(record)
    if not any((r.get('provider_metadata') or {}).get('terminal_usage') is not None
               for r in records):
        return []
    result = []
    for provider, group in sorted(groups.items()):
        observed = []
        for record in group:
            usage = (record.get('provider_metadata') or {}).get('terminal_usage')
            if usage is None:
                continue
            fields = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'total_tokens')
            if not isinstance(usage, dict) or any(
                    type(usage.get(key)) is not int or usage[key] < 0 for key in fields):
                raise ValueError('Invalid native token usage receipt')
            if (usage['input_tokens'] + usage['output_tokens'] != usage['total_tokens']
                    or usage['cached_input_tokens'] > usage['input_tokens']):
                raise ValueError('Native token usage receipt does not reconcile')
            observed.append(usage)
        complete = len(observed) == len(group)
        totals = {key: sum(u[key] for u in observed) if complete else None
                  for key in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'total_tokens')}
        totals['uncached_input_tokens'] = (totals['input_tokens'] - totals['cached_input_tokens']
                                          if complete else None)
        result.append(dict(provider=provider, count=len(group), observed=len(observed), **totals))
    return result
