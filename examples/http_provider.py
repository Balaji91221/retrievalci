"""Controlled loopback provider implementing the RetrievalCI JSON contract.

Start with --snapshot ID --store ARTIFACTS, then use the printed URL with CLI run.
This fixture is a test server, not a hosted retrieval service.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from retrievalci import LexicalRetriever, SnapshotStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--store", default=".retrievalci")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    snapshot = SnapshotStore(args.store).load(args.snapshot)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                if self.path != "/retrieve":
                    self.send_error(404); return
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 100_000:
                    self.send_error(413); return
                request = json.loads(self.rfile.read(size))
                if request.get("snapshot_id") != snapshot.id:
                    self.send_error(409); return
                if not isinstance(request.get("query"), str) or type(request.get("k")) is not int or not 0 < request["k"] <= 1000:
                    self.send_error(400); return
                batch = LexicalRetriever().retrieve(request["query"], request["k"], snapshot)
                payload = {"schema_version": 1, "snapshot_id": batch.snapshot_id,
                    "index_complete": batch.index_complete, "index_id": batch.index_id,
                    "hits": [hit.to_dict() for hit in batch.hits]}
                content = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except (ValueError, KeyError, TypeError):
                self.send_error(400)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Fixture provider: http://127.0.0.1:{server.server_port}/retrieve", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
