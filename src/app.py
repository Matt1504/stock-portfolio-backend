import os
from flask import Flask, jsonify, g, json
from datetime import datetime, timezone
from flask_graphql import GraphQLView
from flask_cors import CORS

from database.database import client
from schemas.schema import schema

app = Flask(__name__)
CORS(app)


@app.before_request
def begin_data_freshness():
    g.request_started = datetime.now(timezone.utc).isoformat()
    g.data_freshness = []


@app.after_request
def expose_data_freshness(response):
    if response.is_json:
        payload = response.get_json(silent=True)
        if isinstance(payload, dict) and 'data' in payload:
            entries = getattr(g, 'data_freshness', [])
            timestamp = min([item[0] for item in entries] + [g.request_started])
            payload.setdefault('extensions', {})['dataFreshness'] = {
                'lastUpdated': timestamp, 'cacheHit': any(item[1] for item in entries)
            }
            response.set_data(json.dumps(payload))
    return response


app.add_url_rule('/graphql', view_func=GraphQLView.as_view('graphql', schema=schema, graphiql=True))


@app.get('/health')
def health():
    try:
        client.admin.command('ping')
    except Exception:
        return jsonify(status='unavailable', database='unavailable'), 503
    return jsonify(status='ok', database='ok')

if __name__ == '__main__':
    app.run(host=os.getenv('HOST', '127.0.0.1'), port=int(os.getenv('PORT', '5002')))
