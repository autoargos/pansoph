#!/usr/bin/env bash
# Seed the demo corpus: create one scoped, ACL'd connector instance per department
# (all backed by the sample MCP server) plus the public GitHub repo, then sync them.
# Idempotent: re-running re-creates and re-syncs. Requires the stack to be up and the
# Keycloak realm seeded (alice is the admin who owns the instances).
#
#   ./deploy/seed-demo.sh
#
set -euo pipefail

AGORA="${AGORA:-localhost:8080}"
KC="${KC:-http://localhost:8090}"
MCP="${MCP_URL:-http://mcp-sample:8000/mcp}"
BASE="$AGORA/api/connectors/instances"

echo "Getting alice's admin token..."
TOKEN=$(curl -s -X POST "$KC/realms/pansoph/protocol/openid-connect/token" \
  -d grant_type=password -d client_id=lens -d username=alice -d password=alice \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
AUTH="authorization: Bearer $TOKEN"
CT="content-type: application/json"

mk() { # id name prefix scope_type scope_ref acl-json
  curl -s -o /dev/null -w "  create %-16s HTTP %{http_code}\n" -X POST "$BASE" -H "$AUTH" -H "$CT" \
    -d "{\"connector\":\"mcp\",\"name\":\"$2\",\"instance_id\":\"$1\",
         \"config\":{\"server_url\":\"$MCP\",\"resource_uri_prefix\":\"$3\",$6},
         \"scope_type\":\"$4\",\"scope_ref\":\"$5\"}" || true
}

echo "Creating department instances..."
mk nw-company     "Northwind Company Wiki" "company://"  org   ""            '"public":true'
mk nw-engineering "Northwind Engineering"  "eng://"      group engineering   '"allow_groups":["engineering"]'
mk nw-product     "Northwind Product"      "product://"  group product       '"allow_groups":["product"]'
mk nw-finance     "Northwind Finance"      "finance://"  group finance       '"allow_groups":["finance"]'
mk nw-hr          "Northwind People & HR"  "hr://"       group hr            '"allow_groups":["hr"]'
mk nw-sales       "Northwind Sales"        "sales://"    group sales         '"allow_groups":["sales"]'
mk nw-security    "Northwind Security"     "security://" group leadership    '"allow_groups":["leadership","engineering"]'

echo "Syncing all instances..."
for id in nw-company nw-engineering nw-product nw-finance nw-hr nw-sales nw-security spoon-knife; do
  curl -s -X POST "$BASE/$id/sync" -H "$AUTH" \
    | python3 -c "import sys,json;d=json.load(sys.stdin);print('  %-16s %s %s docs'%('$id',d.get('status'),d.get('docs_indexed')))" \
    || echo "  $id: not present (skip)"
done

echo "Done. Sign in to lens at http://localhost:3000 as alice / bob / carol / dave."
