"""Parse Wealthsimple CSV offline; optionally preview or import through GraphQL."""
import argparse
import json
import sys
from pathlib import Path
from statement_import.parser import parse_statement
from statement_import.importer import GraphQLClient, prepare_import, apply_import


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', help='UTF-8 Wealthsimple CSV file, or - for stdin')
    parser.add_argument('--endpoint', default='http://127.0.0.1:5002/graphql')
    parser.add_argument('--profile', help='Exact profile name or ID')
    parser.add_argument('--platform', help='Exact CAD platform name or ID')
    parser.add_argument('--account', default='NRSA', help='Account code (default: NRSA)')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--plan', action='store_true', help='Read API metadata/history and preview writes; no mutations')
    mode.add_argument('--apply', action='store_true', help='Preflight and create missing stocks/transactions')
    parser.add_argument('--report', help='Save parsed preview/plan or incremental import audit as JSON')
    args = parser.parse_args()
    if (args.plan or args.apply) and (not args.profile or not args.platform):
        parser.error('--plan/--apply require --profile and --platform')
    if args.apply and not args.report:
        parser.error('--apply requires --report for a persistent audit of partial imports')
    def save(value):
        if args.report:
            path = Path(args.report)
            if args.file != '-' and path.resolve() == Path(args.file).resolve():
                raise ValueError('Report path must differ from the input file')
            temporary = path.with_name(path.name + '.tmp')
            temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
            temporary.replace(path)
    try:
        text = sys.stdin.read() if args.file == '-' else Path(args.file).read_text(encoding='utf-8-sig')
        result = parse_statement(text)
        if not result['errors'] and (args.plan or args.apply):
            client = GraphQLClient(args.endpoint)
            result = prepare_import(client, result, args.profile, args.platform, args.account)
            if args.apply and not result['errors']:
                result = apply_import(client, result, progress=save)
        save(result)
        print(json.dumps(result, indent=2))
        return 1 if result.get('errors') else 0
    except (ValueError, RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
