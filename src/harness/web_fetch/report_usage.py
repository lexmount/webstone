"""Native model usage from immutable per-result receipts."""

from collections import defaultdict


def token_usage(records):
    """Sum observed receipts and retain the observed and expected populations."""
    groups = defaultdict(list)
    for record in records:
        groups[record['provider']].append(record)
    if not any(any((r.get('provider_metadata') or {}).get(key) is not None
                   for key in ('terminal_usage', 'subagent_usage'))
               for r in records):
        return []
    result = []
    for provider, group in sorted(groups.items()):
        observed = []
        for record in group:
            metadata = record.get('provider_metadata') or {}
            usage = metadata.get('terminal_usage', metadata.get('subagent_usage'))
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
        totals = {key: sum(u[key] for u in observed) if observed else None
                  for key in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'total_tokens')}
        totals['uncached_input_tokens'] = (totals['input_tokens'] - totals['cached_input_tokens']
                                          if observed else None)
        result.append(dict(provider=provider, count=len(group), observed=len(observed), **totals))
    return result
