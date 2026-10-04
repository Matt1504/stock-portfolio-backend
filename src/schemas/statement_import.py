"""Statement preview and import through the same public GraphQL operations as CLI."""
import hashlib
import json
import threading
import graphene
from graphene.types.generic import GenericScalar
from graphql import GraphQLError
from schemas.profiles import owned_platform
from statement_import.parser import parse_statement
from statement_import.importer import prepare_import, apply_import

IMPORT_LOCK = threading.Lock()


class InProcessGraphQLClient:
    def __init__(self, context):
        self.context = context

    def execute(self, query, variables=None):
        # Import lazily because these fields are themselves part of the schema.
        from schemas.schema import schema
        result = schema.execute(query, variable_values=variables or {}, context_value=self.context)
        if result.errors:
            raise RuntimeError('; '.join(str(error) for error in result.errors))
        return result.data


class StatementPreview(graphene.ObjectType):
    parsed = GenericScalar(required=True)
    plan = GenericScalar()
    preview_hash = graphene.String()


def preview_statement(info, profile_id, platform_id, text):
    platform = owned_platform(profile_id, platform_id)
    if platform.currency.code != 'CAD':
        raise GraphQLError('This statement format requires a CAD platform.')
    if not text.strip() or len(text) > 200000:
        raise GraphQLError('Paste CSV text of up to 200,000 characters.')
    parsed = parse_statement(text)
    if len(parsed['transactions']) + len(parsed['skipped']) + len(parsed['errors']) > 500:
        raise GraphQLError('Import up to 500 statement rows at a time.')
    if parsed['errors']:
        return StatementPreview(parsed=parsed)
    try:
        plan = prepare_import(InProcessGraphQLClient(info.context), parsed, str(profile_id), str(platform_id), platform.account.code)
    except (ValueError, RuntimeError) as error:
        raise GraphQLError(str(error))
    fingerprint = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return StatementPreview(parsed=parsed, plan=plan, preview_hash=fingerprint)


class ImportStatementMutation(graphene.Mutation):
    report = GenericScalar(required=True)

    class Arguments:
        profile_id = graphene.ID(required=True)
        platform = graphene.ID(required=True)
        text = graphene.String(required=True)
        preview_hash = graphene.String(required=True)

    def mutate(self, info, profile_id, platform, text, preview_hash):
        # Serialize UI imports, then repeat preflight against current records.
        with IMPORT_LOCK:
            preview = preview_statement(info, profile_id, platform, text)
            if not preview.plan or preview.parsed['errors'] or preview.plan['errors']:
                raise GraphQLError('Fix all preview errors before importing.')
            if preview.preview_hash != preview_hash:
                raise GraphQLError('Records changed since preview. Preview again before importing.', extensions={'code': 'IMPORT_PREVIEW_STALE'})
            report = apply_import(InProcessGraphQLClient(info.context), preview.plan)
        return ImportStatementMutation(report=report)
