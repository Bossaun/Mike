"""Reply citations match only supplied context; mutations keep their own gate."""
def allowed_evidence_ids(context):
    ids={'seed'}
    for field in ('current_messages','messages'):
        for message in context.get(field,[]):
            value=message.get('id')
            if type(value) is int or isinstance(value,str) and value:ids.add(str(value))
    return sorted(ids)


def failure_code(exc):
    known={'invalid_result','invalid_evidence','missing_evidence','Daily model budget reached','Model output truncated','Model context limit exceeded; split transcript explicitly'}
    value=str(exc)
    if value in known:return value
    if value.startswith('Model API HTTP '):return 'provider_http'
    if value=='Model API network failure; reservation retained':return 'provider_network'
    return 'processing_error'
