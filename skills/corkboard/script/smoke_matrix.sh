#!/usr/bin/env bash
# smoke_matrix.sh — live integration smoke test against a Corkboard server.
# Reads CORKBOARD_URL, CORKBOARD_TOKEN and CORKBOARD_WORKSPACE from the
# environment.
#
# Content requests use the workspace-scoped grammar
#   {CORKBOARD_URL}/api/v1/o/{org_slug}/{ws_slug}/{path}
# (the old unscoped /api/v1/{path} grammar is dead — it returns 404).
# The workspace scope follows the client's resolution order:
# CORKBOARD_WORKSPACE when set, else the first accessible pair from
# GET /api/v1/me.
# Prints a table: command | exit | key-output
set -euo pipefail

CLI="python3 script/corkboard.py"
NS="smoke"
: "${CORKBOARD_URL:?CORKBOARD_URL must be set}"
: "${CORKBOARD_TOKEN:?CORKBOARD_TOKEN must be set}"

# ------------------------------------------------------------------
# Workspace scope for the raw transport probe in section 3.  The CLI
# resolves its own scope; this mirrors the client's order
# (CORKBOARD_WORKSPACE > first accessible pair from /me).
# ------------------------------------------------------------------
resolve_workspace() {
    python3 - <<'PYEOF'
import sys
sys.path.insert(0, "script")
from cb_client import CorkboardClient, CorkboardError
try:
    org, ws = CorkboardClient().workspace
except CorkboardError as exc:
    sys.stderr.write(f"{exc}\n")
    sys.exit(1)
print(f"{org}/{ws}")
PYEOF
}

if ! WS_SPEC="$(resolve_workspace)"; then
    echo "FATAL: cannot resolve the workspace scope." >&2
    echo "Set CORKBOARD_WORKSPACE=org_slug/ws_slug (see: $CLI me)." >&2
    exit 1
fi
ORG="${WS_SPEC%%/*}"
WS="${WS_SPEC##*/}"
[[ -n "$ORG" && -n "$WS" && "$WS" != "$WS_SPEC" ]] || {
    echo "FATAL: bad workspace spec '$WS_SPEC' (want org_slug/ws_slug)." >&2
    exit 1
}

# header
printf "%-40s | %4s | %s\n" "command" "exit" "key-output"
printf "%-40s-+-%4s-+-%s\n" "----------------------------------------" "----" "--------------------------------------------------"

# 429 pacing: the API throttles at 60 req/min (per token). Space requests out.
: "${PACE:=1.5}"
pace() { sleep "$PACE"; }

# run: execute command, print table row, return exit code implicitly
row() {
    local label="$1"; shift
    local out rc
    set +e
    out="$("$@" 2>&1)"
    rc=$?
    set -e
    local snippet="${out%%$'\n'*}"
    snippet="${snippet:0:80}"
    printf "%-40s | %4s | %s\n" "$label" "$rc" "$snippet"
    pace
}

# raw: capture full output without printing a table row
raw() {
    local out rc
    set +e
    out="$("$@" 2>&1)"
    rc=$?
    set -e
    echo "$out"
    pace
    return $rc
}

# assert: run a check and print an info row
check() {
    local label="$1" result="$2"
    printf "%-40s | %4s | %s\n" "$label" "-" "$result"
}

# ------------------------------------------------------------------
# 1. get on a fresh page id (expect 404-style error, exit 1)
# ------------------------------------------------------------------
row "get (fresh page, 404)" $CLI get "$NS/noexist"

# ------------------------------------------------------------------
# 2. put --text (create) → get (body round-trips) → put again (replace)
# ------------------------------------------------------------------
PAGE="$NS/smoke-test"
row "put --text (create)" $CLI put "$PAGE" --text "hello world"
BODY=$(raw $CLI get "$PAGE") || true
echo "$BODY" | grep -q "hello world" && R="OK" || R="FAIL"
row "get (round-trip)" $CLI get "$PAGE"
check "  round-trip check" "body contains 'hello world': $R"

row "put again (replace)" $CLI put "$PAGE" --text "hello world v2"
BODY2=$(raw $CLI get "$PAGE") || true
echo "$BODY2" | grep -q "hello world v2" && R2="OK" || R2="FAIL"
row "get (after replace)" $CLI get "$PAGE"
check "  replace check" "body contains 'hello world v2': $R2"

# ------------------------------------------------------------------
# 3. CAS conflict (G7): concurrent write preserved through an edit
# ------------------------------------------------------------------
PAGE_CAS="$NS/smoke-cas"
row "put (setup cas)" $CLI put "$PAGE_CAS" --text "alpha
beta
gamma"

# Concurrent writer bypasses the CLI and bumps the revision + changes the body.
# Raw curl on purpose: this row probes the transport itself, so the
# workspace-scoped URL is built by hand from ORG/WS resolved above.
CAS_HTTP="$(curl -s -o /dev/null -w '%{http_code}' -X PUT \
    "$CORKBOARD_URL/api/v1/o/$ORG/$WS/pages/$PAGE_CAS" \
    -H "Authorization: Bearer $CORKBOARD_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"body":"alpha\nCONCURRENT\nbeta\ngamma"}')"
if [[ "$CAS_HTTP" != "200" ]]; then
    echo "FATAL: concurrent-writer PUT returned HTTP $CAS_HTTP" >&2
    echo "  PUT $CORKBOARD_URL/api/v1/o/$ORG/$WS/pages/$PAGE_CAS" >&2
    exit 1
fi

# CLI edit must re-fetch the concurrent body and preserve the writer's text.
row "edit after concurrent write" $CLI edit "$PAGE_CAS" --old "beta" --new "BETA"
CAS_BODY=$(raw $CLI get "$PAGE_CAS") || true
echo "$CAS_BODY" | grep -q "CONCURRENT" && echo "$CAS_BODY" | grep -q "BETA" && CAS_OK="OK" || CAS_OK="FAIL"
row "get (cas result)" $CLI get "$PAGE_CAS"
check "  concurrent text preserved" "CONCURRENT + BETA present: $CAS_OK"

# Deterministic 412: a stale revision forces the mutation re-apply path live.
PAGE_CAS2="$NS/smoke-cas2"
row "put (setup cas2)" $CLI put "$PAGE_CAS2" --text "orig text"
CAS2_OUT=$(python3 - "$PAGE_CAS2" <<'PYEOF' 2>&1
import os, sys
sys.path.insert(0, "script")
from cb_client import CorkboardClient, CorkboardError
c = CorkboardClient()
page_id = sys.argv[1]
page = c.get_page(page_id)
stale_rev = page["revision"]
# A concurrent writer (raw PUT, no If-Match) bumps revision + body.
c.request("PUT", f"pages/{page_id}", data={"body": "concurrent replacement"})
def mutate(fresh):
    return fresh + " [edited]"
try:
    r = c.put_cas(page_id, mutate, revision=stale_rev)
    print("RETRY_OK:" + r["body"])
except CorkboardError as e:
    print("CONFLICT:" + str(e.status))
PYEOF
)
echo "$CAS2_OUT" | grep -q "concurrent replacement \[edited\]" && CAS2_R="OK" || CAS2_R="FAIL"
check "  412 re-apply (stale rev)" "mutate re-applied to fresh body: $CAS2_R ($CAS2_OUT)"

# ------------------------------------------------------------------
# 4. append
# ------------------------------------------------------------------
PAGE_APPEND="$NS/smoke-append"
row "put (setup append)" $CLI put "$PAGE_APPEND" --text "part one"
row "append --text" $CLI append "$PAGE_APPEND" --text " - part two"
APPEND_BODY=$(raw $CLI get "$PAGE_APPEND") || true
echo "$APPEND_BODY" | grep -q "part one" && echo "$APPEND_BODY" | grep -q "part two" && AP_OK="OK" || AP_OK="FAIL"
row "get (append result)" $CLI get "$PAGE_APPEND"
check "  append both parts" "contains both parts: $AP_OK"

# ------------------------------------------------------------------
# 5. edit
# ------------------------------------------------------------------
PAGE_EDIT="$NS/smoke-edit"
row "put (setup edit)" $CLI put "$PAGE_EDIT" --text "line alpha
line beta
line delta"

row "edit --old/--new (happy)" $CLI edit "$PAGE_EDIT" --old "line alpha" --new "line gamma"
EDIT_BODY=$(raw $CLI get "$PAGE_EDIT") || true
echo "$EDIT_BODY" | grep -q "line gamma" && ! echo "$EDIT_BODY" | grep -q "line alpha" && ED_H="OK" || ED_H="FAIL"
check "  edit happy path" "alpha→gamma (alpha gone, gamma present): $ED_H"

row "edit 0-match abort" $CLI edit "$PAGE_EDIT" --old "no such text anywhere" --new "replaced"
row "edit multi-match abort" $CLI edit "$PAGE_EDIT" --old "line" --new "LINE"

# ------------------------------------------------------------------
# 6. insert
# ------------------------------------------------------------------
PAGE_INSERT="$NS/smoke-insert"
row "put (setup insert)" $CLI put "$PAGE_INSERT" --text "# Section One
content here
# Section Two
more content"

row "insert --under" $CLI insert "$PAGE_INSERT" --under "# Section One" --text "inserted text"
INS_BODY=$(raw $CLI get "$PAGE_INSERT") || true
echo "$INS_BODY" | grep -q "inserted text" && INS_OK="OK" || INS_OK="FAIL"
check "  insert under heading" "inserted text found: $INS_OK"

row "insert --after" $CLI insert "$PAGE_INSERT" --after "content here" --text "after line"
row "insert --before" $CLI insert "$PAGE_INSERT" --before "more content" --text "before line"
row "insert anchor-miss" $CLI insert "$PAGE_INSERT" --under "# No Such Heading" --text "nope"

# ------------------------------------------------------------------
# 7. find
# ------------------------------------------------------------------
PAGE_FIND="$NS/smoke-find"
row "put (setup find)" $CLI put "$PAGE_FIND" --text "apple
Banana
APPLE
cherry"

row "find literal apple" $CLI find "$PAGE_FIND" "apple"
row "find -E regex" $CLI find "$PAGE_FIND" -E "[Bb]anana"
row "find -i case-insensitive" $CLI find "$PAGE_FIND" -i "apple"

# ------------------------------------------------------------------
# 8. move page
# ------------------------------------------------------------------
PAGE_A="$NS/smoke-move-a"
PAGE_B="$NS/smoke-move-b"
PAGE_LINK="$NS/smoke-move-link"

row "put (page A)" $CLI put "$PAGE_A" --text "I am page A"
row "put (page B)" $CLI put "$PAGE_B" --text "I am page B"
row "put (link page)" $CLI put "$PAGE_LINK" --text "See [$PAGE_B]($PAGE_B) for details"

NEW_PAGE="$NS/smoke-moved"
row "move page" $CLI move "$PAGE_B" "$NEW_PAGE"

row "get old id (moved)" $CLI get "$PAGE_B"
MOVE_BODY=$(raw $CLI get "$NEW_PAGE") || true
echo "$MOVE_BODY" | grep -q "page B" && M_GET="OK" || M_GET="FAIL"
row "get new id (moved)" $CLI get "$NEW_PAGE"
check "  move get new id" "body found at new id: $M_GET"

LINK_BODY=$(raw $CLI get "$PAGE_LINK") || true
echo "$LINK_BODY" | grep -q "$NEW_PAGE" && L_RW="OK" || L_RW="FAIL"
check "  link rewritten" "link target updated: $L_RW"

# ------------------------------------------------------------------
# 9. links / backlinks
# ------------------------------------------------------------------
row "links" $CLI links "$PAGE_LINK"
row "backlinks" $CLI backlinks "$NEW_PAGE"

# ------------------------------------------------------------------
# 10. revisions
# ------------------------------------------------------------------
REV_PAGE="$NS/smoke-rev"
row "put (setup rev)" $CLI put "$REV_PAGE" --text "revision one"
row "put (rev 2)" $CLI put "$REV_PAGE" --text "revision two"

row "revisions list" $CLI revisions "$REV_PAGE"
row "revision-show" $CLI revision-show "$REV_PAGE" 1

# ------------------------------------------------------------------
# 11. list, search, sitemap, wanted, orphans
# ------------------------------------------------------------------
row "list --ns" $CLI list --ns "$NS"
row "list --ns --depth 1" $CLI list --ns "$NS" --depth 1
row "search" $CLI search "hello"

row "sitemap" $CLI sitemap --ns "$NS"

WANTED_PAGE="$NS/smoke-wanted"
row "put (wanted page)" $CLI put "$WANTED_PAGE" --text "Link to [missing]($NS/nonexistent-target)"
WANTED_OUT=$(raw $CLI wanted) || true
echo "$WANTED_OUT" | grep -q "nonexistent-target" && WANTED_OK="OK" || WANTED_OK="FAIL"
row "wanted" $CLI wanted
check "  wanted detects broken link" "nonexistent-target listed: $WANTED_OK"

ORPHAN_PAGE="$NS/smoke-orphan"
row "put (orphan page)" $CLI put "$ORPHAN_PAGE" --text "nobody links to me"
row "orphans" $CLI orphans

# ------------------------------------------------------------------
# 12. semantic
# ------------------------------------------------------------------
row "semantic" $CLI semantic "test query"

# ------------------------------------------------------------------
# 13. media
# ------------------------------------------------------------------
MEDIA_ID="$NS/smoke-test.png"

python3 -c "
import struct, zlib
def chunk(ctype, data):
    c = ctype + data
    return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
sig = b'\\x89PNG\\r\\n\\x1a\\n'
ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
idat = chunk(b'IDAT', zlib.compress(b'\\x00\\xff\\x00\\x00'))
iend = chunk(b'IEND', b'')
with open('/tmp/smoke-test.png', 'wb') as f:
    f.write(sig + ihdr + idat + iend)
" 2>/dev/null

row "media-upload" $CLI media-upload /tmp/smoke-test.png "$NS" "smoke-test.png"
row "media-list" $CLI media-list --ns "$NS"

$CLI media-get "$MEDIA_ID" -o /tmp/smoke-test-dl.png > /dev/null 2>&1
cmp -s /tmp/smoke-test.png /tmp/smoke-test-dl.png && MG="OK" || MG="FAIL"
check "  media-get byte round-trip" "cmp identical: $MG"

# media-usage: create a page referencing the uploaded media, then verify
# the usage response lists that page.
PAGE_REF="$NS/smoke-media-ref"
row "put (page referencing media)" $CLI put "$PAGE_REF" --text "image: $MEDIA_ID"
USAGE_REF=$(raw $CLI media-usage "$MEDIA_ID") || true
echo "$USAGE_REF" | grep -q "page_id" && UREF_OK="OK" || UREF_OK="FAIL"
row "media-usage (referenced)" $CLI media-usage "$MEDIA_ID"
check "  media-usage referenced" "page_id in used_by: $UREF_OK"

# Unreferenced media returns empty used_by.
UNREF_MEDIA="$NS/smoke-unref.png"
$CLI media-upload /tmp/smoke-test.png "$NS" "smoke-unref.png" > /dev/null 2>&1
USAGE_EMPTY=$(raw $CLI media-usage "$UNREF_MEDIA") || true
echo "$USAGE_EMPTY" | grep -q "'used_by': \[\]" && UEMPTY_OK="OK" || UEMPTY_OK="FAIL"
row "media-usage (unreferenced)" $CLI media-usage "$UNREF_MEDIA"
check "  media-usage unreferenced" "used_by empty: $UEMPTY_OK"
$CLI media-delete "$UNREF_MEDIA" > /dev/null 2>&1
row "media-orphans" $CLI media-orphans

NEW_MEDIA="$NS/smoke-test-moved.png"
row "media-move" $CLI media-move "$MEDIA_ID" "$NEW_MEDIA"
$CLI media-get "$NEW_MEDIA" -o /tmp/smoke-test-moved.png > /dev/null 2>&1
cmp -s /tmp/smoke-test.png /tmp/smoke-test-moved.png && MMG="OK" || MMG="FAIL"
check "  media-get moved bytes" "moved bytes identical: $MMG"

row "media-delete" $CLI media-delete "$NEW_MEDIA"

# ------------------------------------------------------------------
# 14. delete page
# ------------------------------------------------------------------
DEL_PAGE="$NS/smoke-delete"
row "put (setup delete)" $CLI put "$DEL_PAGE" --text "to be deleted"
row "delete page" $CLI delete "$DEL_PAGE"
row "get deleted (fail)" $CLI get "$DEL_PAGE"

# cleanup
rm -f /tmp/smoke-test.png /tmp/smoke-test-dl.png /tmp/smoke-test-moved.png

echo ""
echo "=== smoke matrix complete ==="