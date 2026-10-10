#!/usr/bin/env bash
# check-queue.sh — cindy preRunHook for the Mivo QA → lifeline hand-off on mini.
#
# Pure bash + gh (no model calls, no jq required: JSON is handled by gh's
# built-in --jq). Compatible with macOS /bin/bash 3.2.
#
# Reads the open issues labelled `cindy-dispatch` in PraiseZhu/pr-qa-agent-deploy,
# picks the OLDEST one that carries a parseable ```json mivo-qa-finding``` block,
# and writes it as a lifeline observation that
#   node scripts/loops/lifeline/doctor.mjs ingest --config CONFIG --input OBSERVATION
# accepts (validateObservation/verifyObservation in scripts/loops/lifeline/state.mjs):
#   <dir>/issue-<N>/evidence-<sha16>.json     immutable evidence payload (issue + finding)
#   <dir>/issue-<N>/observation-<sha16>.json  observation; evidence.{path,sha256} -> payload
# Both files MUST live under the lifeline config's evidenceRoot (no symlinked
# ancestors), otherwise ingest fails with path-outside-root / symlink-forbidden.
#
# stdout (fed to the cindy agent as untrusted data):
#   line 1: absolute path of the observation file (the --input for ingest)
#   then MIVO_QA_*=... key/value lines, then CINDY_PRECHECK_OK
#
# Exit codes (cindy preRunHook protocol: 0 = run, 2 = skip, anything else = blocked + notified):
#   0  an issue was written; run the agent
#   2  queue empty (no open cindy-dispatch issues); skip this fire, zero tokens
#   3  gh failed (not installed, not authenticated, network, API error)
#   4  queue not empty but no issue has a usable mivo-qa-finding block (needs a human)
#   5  local error (evidence dir unusable, no sha256 tool, unsafe path, write failure)
#
# Environment (all optional):
#   MIVO_QA_QUEUE_REPO        default PraiseZhu/pr-qa-agent-deploy
#   MIVO_QA_QUEUE_LABEL       default cindy-dispatch
#   MIVO_QA_LIFELINE_CONFIG   lifeline config JSON; if set and MIVO_QA_EVIDENCE_DIR is not,
#                             the evidence dir defaults to <config.evidenceRoot>/mivo-qa
#                             (evidenceRoot is read with jq or node, whichever exists)
#   MIVO_QA_EVIDENCE_DIR      absolute dir for the files above
#                             (fallback default: $HOME/mivo-ops/evidence/mivo-qa)
#   MIVO_QA_SOURCE            observation.source, default mivo-qa
#   GH_BIN                    gh executable, default: gh on PATH
#
# Deliberately no `set -e`: bash would exit with the failing command's status,
# and a stray status 2 would be misread by cindy as "queue empty, skip".
set -u -o pipefail

EX_RUN=0 EX_EMPTY=2 EX_GH=3 EX_UNPARSEABLE=4 EX_LOCAL=5

# GUI-launched cindy (Electron) on macOS often has a minimal PATH.
for _p in /opt/homebrew/bin /usr/local/bin; do
  case ":${PATH:-}:" in *":${_p}:"*) ;; *) [ -d "$_p" ] && PATH="${_p}:${PATH:-}" ;; esac
done
export PATH

QUEUE_REPO="${MIVO_QA_QUEUE_REPO:-PraiseZhu/pr-qa-agent-deploy}"
QUEUE_LABEL="${MIVO_QA_QUEUE_LABEL:-cindy-dispatch}"
OBS_SOURCE="${MIVO_QA_SOURCE:-mivo-qa}"
LIFELINE_CONFIG="${MIVO_QA_LIFELINE_CONFIG:-}"
GH="${GH_BIN:-gh}"

die() { local code="$1"; shift; printf 'check-queue: %s\n' "$*" >&2; exit "$code"; }

# ---------------------------------------------------------------- preconditions
case "$OBS_SOURCE" in
  ''|*[!A-Za-z0-9._:-]*) die "$EX_LOCAL" "MIVO_QA_SOURCE must match [A-Za-z0-9._:-]+" ;;
esac
case "$QUEUE_REPO" in
  */*) ;; *) die "$EX_LOCAL" "MIVO_QA_QUEUE_REPO must be owner/name" ;;
esac
command -v "$GH" >/dev/null 2>&1 || die "$EX_GH" "gh not found (GH_BIN=${GH}); install/login gh on this machine"

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
  elif command -v openssl >/dev/null 2>&1; then openssl dgst -sha256 "$1" | sed 's/^.*= *//'
  else return 1
  fi
}

read_evidence_root() {
  # $1 = lifeline config path; prints evidenceRoot
  if command -v jq >/dev/null 2>&1; then
    jq -er '.evidenceRoot | strings' "$1"
  elif command -v node >/dev/null 2>&1; then
    node -e 'const r=JSON.parse(require("fs").readFileSync(process.argv[1],"utf8")).evidenceRoot;if(typeof r!=="string"||!r)process.exit(1);process.stdout.write(r+"\n")' "$1"
  else
    return 1
  fi
}

if [ -n "${MIVO_QA_EVIDENCE_DIR:-}" ]; then
  EVIDENCE_DIR="$MIVO_QA_EVIDENCE_DIR"
elif [ -n "$LIFELINE_CONFIG" ]; then
  [ -r "$LIFELINE_CONFIG" ] || die "$EX_LOCAL" "MIVO_QA_LIFELINE_CONFIG not readable: $LIFELINE_CONFIG"
  _root="$(read_evidence_root "$LIFELINE_CONFIG")" || die "$EX_LOCAL" "cannot read evidenceRoot from $LIFELINE_CONFIG (need jq or node)"
  EVIDENCE_DIR="${_root%/}/mivo-qa"
else
  EVIDENCE_DIR="${HOME:-/nonexistent}/mivo-ops/evidence/mivo-qa"
fi
case "$EVIDENCE_DIR" in
  /*) ;; *) die "$EX_LOCAL" "evidence dir must be absolute: $EVIDENCE_DIR" ;;
esac
# The path is embedded verbatim in JSON below: refuse characters that would need escaping.
case "$EVIDENCE_DIR" in
  *'"'*|*\\*|*[[:cntrl:]]*) die "$EX_LOCAL" "evidence dir contains characters unsafe for JSON: $EVIDENCE_DIR" ;;
esac
EVIDENCE_DIR="${EVIDENCE_DIR%/}"

# ---------------------------------------------------------------- query queue
# gh's embedded jq (gojq) parses, validates and orders the queue. Output:
#   MQ_META <tab> total <tab> skipped(list or -)
#   MQ_FIELDS <tab> number url createdAt repo sha caseId outcomeCode status sev fp dim   (only if a usable issue exists)
#   MQ_EVIDENCE <tab> <compact JSON evidence payload>                                     (idem)
# Every MQ_FIELDS value is validated to a tab/newline/quote-free charset here.
# shellcheck disable=SC2016  # $vars below are jq variables, not shell.
JQ_PROGRAM='
def queue_repo: "__QUEUE_REPO__";
def re_block: "```json mivo-qa-finding\\r?\\n(?<j>[\\s\\S]*?)\\r?\\n```";
def clean($n): tostring | gsub("[^A-Za-z0-9._:-]"; "-") | .[0:$n];
def okstr($re): type == "string" and test($re);
def assess:
  . as $i
  | (try ([($i.body // "") | capture(re_block)][0].j | fromjson) catch null) as $f
  | if ($f | type) != "object" then {ok: false, n: $i.number, why: "no-finding-block"}
    elif ($f.schema // "") != "mivo-qa-finding/v1" then {ok: false, n: $i.number, why: "unknown-schema"}
    elif ($f.fingerprint | okstr("^[A-Za-z0-9-]{1,100}$")) | not then {ok: false, n: $i.number, why: "bad-fingerprint"}
    elif ($f.pr.tested_sha | okstr("^[a-f0-9]{40}$")) | not then {ok: false, n: $i.number, why: "bad-tested-sha"}
    elif ($f.repo | IN("xindong/mivo-canvas-plugin", "xindong/mivo-canvas", "makecindy/cindy")) | not then {ok: false, n: $i.number, why: "repo-not-lifeline"}
    elif ($i.createdAt | okstr("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z$")) | not then {ok: false, n: $i.number, why: "bad-createdAt"}
    elif ($i.url | okstr("^https://[A-Za-z0-9./_-]+$")) | not then {ok: false, n: $i.number, why: "bad-url"}
    else
      ($f.source // "check") as $src
      | ($f.status // "") as $st
      | {ok: true, n: $i.number,
         fields: [
           ($i.number | tostring), $i.url, $i.createdAt, $f.repo, $f.pr.tested_sha,
           ("mivo-qa." + (($f.check_id // $src // "unknown") | clean(140))),
           ("MIVO_QA_" + ($f.fingerprint | ascii_upcase)),
           (if ($st | IN("setup-failed", "timeout")) then "ENV_BLOCKED"
            elif $st == "unavailable" then "NOT_RUN"
            else "PRODUCT_FAIL" end),
           (if $src == "code-review" then "unverified"
            elif ($f.severity | IN("P0", "P1", "P2", "P3")) then $f.severity
            else "unverified" end),
           $f.fingerprint,
           (($f.dimension // $f.check_id // "unknown") | clean(160))
         ],
         evidence: {
           schema: "mivo-qa-lifeline-evidence/v1",
           queue: {repo: queue_repo, issue: $i.number, url: $i.url, title: $i.title, createdAt: $i.createdAt},
           finding: $f,
           issueBody: $i.body
         }}
    end;
[ .[] | select(.number != null) ] | sort_by(.createdAt, .number) as $all
| ($all | map(assess)) as $r
| ($r | map(select(.ok)) | .[0]) as $pick
| ($r | map(select(.ok | not) | "#\(.n):\(.why)") | join(",")) as $skipped
| ("MQ_META\t\($all | length)\t\(if $skipped == "" then "-" else $skipped end)"),
  (if $pick then ("MQ_FIELDS\t" + ($pick.fields | join("\t"))), ("MQ_EVIDENCE\t" + ($pick.evidence | tojson)) else empty end)
'
# gh --jq has no --arg; inject the queue repo as a JSON string literal.
case "$QUEUE_REPO" in *[!A-Za-z0-9._/-]*) die "$EX_LOCAL" "MIVO_QA_QUEUE_REPO has unexpected characters" ;; esac
JQ_PROGRAM="${JQ_PROGRAM/__QUEUE_REPO__/${QUEUE_REPO}}"

GH_ERR="$(mktemp "${TMPDIR:-/tmp}/check-queue.gh-err.XXXXXX")" || die "$EX_LOCAL" "mktemp failed"
trap 'rm -f "$GH_ERR"' EXIT

OUT="$("$GH" issue list -R "$QUEUE_REPO" --label "$QUEUE_LABEL" --state open --limit 1000 \
        --json number,title,body,createdAt,url,labels --jq "$JQ_PROGRAM" 2>"$GH_ERR")"
GH_STATUS=$?
if [ "$GH_STATUS" -ne 0 ]; then
  printf 'check-queue: gh issue list -R %s --label %s failed (exit %s): %s\n' \
    "$QUEUE_REPO" "$QUEUE_LABEL" "$GH_STATUS" "$(tr '\n' ' ' <"$GH_ERR" | cut -c1-600)" >&2
  exit "$EX_GH"
fi

META="" FIELDS="" EVIDENCE_JSON=""
while IFS= read -r line; do
  case "$line" in
    MQ_META"	"*) META="${line#MQ_META	}" ;;
    MQ_FIELDS"	"*) FIELDS="${line#MQ_FIELDS	}" ;;
    MQ_EVIDENCE"	"*) EVIDENCE_JSON="${line#MQ_EVIDENCE	}" ;;
  esac
done <<EOF_OUT
$OUT
EOF_OUT

[ -n "$META" ] || die "$EX_GH" "unexpected gh output (no MQ_META line)"
IFS='	' read -r TOTAL SKIPPED <<EOF_META
$META
EOF_META

if [ "${TOTAL:-0}" = "0" ]; then
  printf 'MIVO_QA_QUEUE=empty (%s label:%s)\nCINDY_PRECHECK_OK\n' "$QUEUE_REPO" "$QUEUE_LABEL"
  exit "$EX_EMPTY"
fi
if [ -z "$FIELDS" ] || [ -z "$EVIDENCE_JSON" ]; then
  die "$EX_UNPARSEABLE" "${TOTAL} open ${QUEUE_LABEL} issue(s) in ${QUEUE_REPO}, none with a usable mivo-qa-finding block: ${SKIPPED}"
fi

IFS='	' read -r NUM URL CREATED_AT OBS_REPO SOURCE_SHA CASE_ID OUTCOME_CODE OBS_STATUS SOURCE_SEV FINGERPRINT FAIL_DIM <<EOF_FIELDS
$FIELDS
EOF_FIELDS
[ -n "${FAIL_DIM:-}" ] || die "$EX_GH" "unexpected gh output (incomplete MQ_FIELDS)"

# ---------------------------------------------------------------- write files
ISSUE_DIR="${EVIDENCE_DIR}/issue-${NUM}"
mkdir -p "$ISSUE_DIR" 2>/dev/null || die "$EX_LOCAL" "cannot create $ISSUE_DIR"

TMP_EVIDENCE="$(mktemp "${ISSUE_DIR}/.evidence.XXXXXX")" || die "$EX_LOCAL" "cannot write in $ISSUE_DIR"
trap 'rm -f "$GH_ERR" "${TMP_EVIDENCE:-}" "${TMP_OBS:-}"' EXIT
printf '%s\n' "$EVIDENCE_JSON" >"$TMP_EVIDENCE" || die "$EX_LOCAL" "write failed: $TMP_EVIDENCE"
EVIDENCE_SHA="$(sha256_file "$TMP_EVIDENCE")" || die "$EX_LOCAL" "no sha256 tool (sha256sum/shasum/openssl)"
case "$EVIDENCE_SHA" in
  [0-9a-f]*) [ "${#EVIDENCE_SHA}" -eq 64 ] || die "$EX_LOCAL" "bad sha256 output" ;;
  *) die "$EX_LOCAL" "bad sha256 output" ;;
esac
SHORT="$(printf '%s' "$EVIDENCE_SHA" | cut -c1-16)"
EVIDENCE_PATH="${ISSUE_DIR}/evidence-${SHORT}.json"
OBS_PATH="${ISSUE_DIR}/observation-${SHORT}.json"

# Content-addressed and immutable: never rewrite an existing evidence file
# (lifeline rejects evidence whose bytes/inode change while it is being read).
if [ -f "$EVIDENCE_PATH" ] && [ "$(sha256_file "$EVIDENCE_PATH")" = "$EVIDENCE_SHA" ]; then
  rm -f "$TMP_EVIDENCE"
else
  chmod 0644 "$TMP_EVIDENCE" 2>/dev/null
  mv -f "$TMP_EVIDENCE" "$EVIDENCE_PATH" || die "$EX_LOCAL" "cannot place $EVIDENCE_PATH"
fi
TMP_EVIDENCE=""

# One queue issue = one lifeline observation id: re-runs (or an edited issue body)
# come back from ingest as {"duplicate": true} instead of piling up observations.
OBS_ID="mivo-qa-issue-${NUM}"
if [ ! -f "$OBS_PATH" ]; then
  TMP_OBS="$(mktemp "${ISSUE_DIR}/.observation.XXXXXX")" || die "$EX_LOCAL" "cannot write in $ISSUE_DIR"
  {
    printf '{\n'
    printf '  "schemaVersion": 1,\n'
    printf '  "id": "%s",\n' "$OBS_ID"
    printf '  "repo": "%s",\n' "$OBS_REPO"
    printf '  "source": "%s",\n' "$OBS_SOURCE"
    printf '  "sourceSha": "%s",\n' "$SOURCE_SHA"
    printf '  "caseId": "%s",\n' "$CASE_ID"
    printf '  "outcomeCode": "%s",\n' "$OUTCOME_CODE"
    printf '  "status": "%s",\n' "$OBS_STATUS"
    printf '  "sourceSeverity": "%s",\n' "$SOURCE_SEV"
    printf '  "observedAt": "%s",\n' "$CREATED_AT"
    printf '  "evidence": { "path": "%s", "sha256": "%s" },\n' "$EVIDENCE_PATH" "$EVIDENCE_SHA"
    printf '  "semantic": { "assertionId": "%s", "failureDimension": "%s", "oracleRevision": "mivo-qa-finding/v1" },\n' "$FINGERPRINT" "$FAIL_DIM"
    printf '  "queueIssue": { "repo": "%s", "number": %s, "url": "%s" }\n' "$QUEUE_REPO" "$NUM" "$URL"
    printf '}\n'
  } >"$TMP_OBS" || die "$EX_LOCAL" "write failed: $TMP_OBS"
  chmod 0644 "$TMP_OBS" 2>/dev/null
  mv -f "$TMP_OBS" "$OBS_PATH" || die "$EX_LOCAL" "cannot place $OBS_PATH"
  TMP_OBS=""
fi

# ---------------------------------------------------------------- report
printf '%s\n' "$OBS_PATH"
printf 'MIVO_QA_OBSERVATION=%s\n' "$OBS_PATH"
printf 'MIVO_QA_EVIDENCE=%s\n' "$EVIDENCE_PATH"
printf 'MIVO_QA_ISSUE=%s#%s\n' "$QUEUE_REPO" "$NUM"
printf 'MIVO_QA_ISSUE_URL=%s\n' "$URL"
printf 'MIVO_QA_FINGERPRINT=%s\n' "$FINGERPRINT"
[ -n "$LIFELINE_CONFIG" ] && printf 'MIVO_QA_LIFELINE_CONFIG=%s\n' "$LIFELINE_CONFIG"
printf 'MIVO_QA_QUEUE_DEPTH=%s\n' "$TOTAL"
[ "$SKIPPED" != "-" ] && printf 'MIVO_QA_SKIPPED_UNPARSEABLE=%s\n' "$SKIPPED"
printf 'CINDY_PRECHECK_OK\n'
exit "$EX_RUN"
