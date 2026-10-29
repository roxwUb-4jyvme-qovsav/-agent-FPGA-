"""Offline SDK fixture. Not a real knowledge connector or trusted source."""
import datetime
import hashlib
import json
import sys

TEXT = 'TEST FIXTURE ONLY: capture samples use little-endian order.'
SOURCE = '66666666-6666-4666-8666-666666666666'
def respond(request):
    identifier = request.get('invocation_id')
    output = None
    diagnostics = []
    status = 'completed'
    if request.get('schema_version') != '1.0' or request.get('operation') != 'search':
        status = 'denied'; diagnostics.append('Unsupported fixture request')
    elif request.get('capability') != 'read_knowledge':
        status = 'denied'; diagnostics.append('Fixture capability denied')
    else:
        output = {'hits': [{'source_id': SOURCE, 'source_uri': 'fixture://knowledge/little-endian',
            'locator': 'fixture paragraph 1', 'content_sha256': hashlib.sha256(TEXT.encode()).hexdigest(),
            'retrieved_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'license': 'fixture-only', 'acl_scope': 'fixture-only', 'text': TEXT}],
            'partial': False, 'warnings': ['Fixture data; never use as engineering evidence']}
    return {'schema_version': '1.0', 'invocation_id': identifier, 'status': status,
        'output_schema': 'urn:fpga:contracts:SearchResponse:1.0', 'output': output,
        'diagnostics': diagnostics, 'request_id': None}
if __name__ == '__main__':
    for line in sys.stdin:
        try:
            value = json.loads(line)
            print(json.dumps(respond(value), ensure_ascii=False), flush=True)
        except (ValueError, TypeError):
            print('Invalid fixture input', file=sys.stderr, flush=True)
