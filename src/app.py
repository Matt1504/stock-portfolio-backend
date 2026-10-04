import os
from flask import Flask, jsonify
from flask_graphql import GraphQLView
from flask_cors import CORS

from database.database import client
from schemas.schema import schema

app = Flask(__name__)
CORS(app)


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
