"""Locate restored Notion IDs in an n8n export without executing or modifying it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
from urllib.parse import urlsplit

UUID_TOKEN = re.compile(r'(?<![0-9a-z])(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32})(?![0-9a-z])', re.I)
NOTION = 'n8n-nodes-base.notion'
VERSIONS = (2, 2.1, 2.2)


def canonical(value):
    if not isinstance(value, str) or not UUID_TOKEN.fullmatch(value):
        raise ValueError('Resource map requires explicit UUID strings')
    return str(uuid.UUID(value))


def validate_map(records):
    mapped, destinations = {}, {}
    for record in records:
        if set(record) != {'old_id', 'new_id', 'resource_type'}:
            raise ValueError('Unexpected resource-map fields')
        kind = record['resource_type']
        if kind not in ('database', 'page', 'block', 'data_source'):
            raise ValueError('Unknown resource type')
        old, new = canonical(record['old_id']), canonical(record['new_id'])
        entry = {'new_id': new, 'resource_type': kind}
        if old in mapped and mapped[old] != entry:
            raise ValueError('Contradictory old resource mapping')
        if new in destinations and destinations[new] != old:
            raise ValueError('Many-to-one resource mapping needs explicit review')
        mapped[old], destinations[new] = entry, old
    return mapped


def walk(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, path + (str(index),))
    elif isinstance(value, str):
        yield path, value


def pointer(parts):
    return '/' + '/'.join(p.replace('~', '~0').replace('/', '~1') for p in parts)


def literal_database_target(value, old):
    if UUID_TOKEN.fullmatch(value):
        return canonical(value) == old
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or parsed.hostname not in ('www.notion.so', 'notion.so', 'www.notion.com', 'notion.com'):
        return False
    # A view/query/fragment may have its own identity. Do not guess how to migrate it.
    try:
        has_port = parsed.port is not None
    except ValueError:
        return False
    if parsed.query or parsed.fragment or parsed.username or parsed.password or has_port:
        return False
    tokens = UUID_TOKEN.findall(parsed.path)
    return len(tokens) == 1 and canonical(tokens[0]) == old


def inspect(workflow, records):
    mapped = validate_map(records)
    if not isinstance(workflow, dict) or not isinstance(workflow.get('nodes'), list):
        raise ValueError('Expected an exported workflow with a nodes array')
    findings, limitations = [], []
    for index, node in enumerate(workflow['nodes']):
        if not isinstance(node, dict) or not isinstance(node.get('parameters', {}), dict):
            raise ValueError('Malformed node')
        supported = node.get('type') == NOTION and node.get('typeVersion') in VERSIONS
        parameters = node.get('parameters', {})
        resource = parameters.get('resource')
        # n8n omits default operations from exports; the verified database default is get.
        operation = parameters.get('operation', 'get' if resource == 'database' else None)
        database_operation = (resource, operation) in (
            ('database', 'get'), ('databasePage', 'create'), ('databasePage', 'getAll'))
        if node.get('type') == NOTION and not supported:
            limitations.append({'node_index': index, 'reason': 'Notion node version lacks a verified adapter'})
        for path, value in walk(parameters):
            dynamic = value.startswith('=') or '{{' in value
            direct = path in (('databaseId',), ('databaseId', 'value'))
            hits = sorted({canonical(token) for token in UUID_TOKEN.findall(value)} & mapped.keys())
            if supported and direct and not hits:
                limitations.append({'pointer': pointer(('nodes', str(index), 'parameters') + path),
                    'reason': 'Dynamic target cannot be resolved offline' if dynamic else 'Target absent from supplied map'})
            for old in hits:
                target = mapped[old]
                if old == target['new_id']:
                    continue
                status = 'reference_requires_review'
                # A lookalike field in another node or nested payload can be live data.
                if supported and path in (('databaseId', 'cachedResultUrl'),
                                           ('databaseId', 'cachedResultName')):
                    status = 'cached_metadata_only'
                elif dynamic:
                    status = 'dynamic_expression_requires_review'
                elif supported and direct and database_operation:
                    if target['resource_type'] != 'database':
                        status = 'resource_type_mismatch'
                    elif literal_database_target(value, old):
                        status = 'literal_database_rebind_candidate'
                if node.get('disabled') is True:
                    status = 'disabled_node_reference'
                finding = {'node_index': index,
                    'pointer': pointer(('nodes', str(index), 'parameters') + path),
                    'status': status, 'old_id': old, 'new_id': target['new_id']}
                if status == 'literal_database_rebind_candidate':
                    finding['proposed_locator'] = {'__rl': True, 'mode': 'id', 'value': target['new_id']}
                findings.append(finding)
    return {'schema': 'restore-link-check-v1', 'findings': findings, 'limitations': limitations,
            'runtime_recovery_verified': False,
            'coverage': 'String values inside node parameters and IDs in supplied map only; credentials and execution data excluded',
            'required_next_checks': ['Validate map against the actual restore receipt',
                'Check destination permissions, properties, dynamic expressions and other integrations',
                'Review proposed changes and test a safe representative workflow in its owning app']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workflow', type=Path)
    parser.add_argument('resource_map', type=Path)
    parser.add_argument('report', type=Path)
    args = parser.parse_args()
    if args.report.resolve() in (args.workflow.resolve(), args.resource_map.resolve()):
        raise ValueError('Report must not replace an input')
    raw = [args.workflow.read_bytes(), args.resource_map.read_bytes()]
    result = inspect(*(json.loads(value) for value in raw))
    result['input_sha256'] = [hashlib.sha256(value).hexdigest() for value in raw]
    # Publish a complete report atomically and refuse to replace previous evidence.
    fd, temporary = tempfile.mkstemp(dir=args.report.parent, prefix='.restore-link-check-')
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(result, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, args.report)
    finally:
        os.unlink(temporary)
    print(json.dumps({'report': str(args.report.resolve()), 'findings': len(result['findings']),
                      'runtime_recovery_verified': False}))


if __name__ == '__main__':
    main()
