#!/usr/bin/env bash
set -euo pipefail

UNIT="${OMNITRADE_ACTIVATION_UNIT:-omnitrade-orchestration.service}"
SYSTEMCTL="${OMNITRADE_SYSTEMCTL:-systemctl}"
DROPIN_DIR="${OMNITRADE_ACTIVATION_DROPIN_DIR:-/etc/systemd/system/${UNIT}.d}"
STATE_DIR="${OMNITRADE_ACTIVATION_STATE_DIR:-/etc/omnitrade/activation-only}"
DROPIN_FILE="${DROPIN_DIR}/zz-activation-only-selector.conf"
CURRENT_FILE="${STATE_DIR}/current.env"
OFF_FILE="${STATE_DIR}/off.env"
ON_FILE="${STATE_DIR}/on.env"
PINNED_ON_FILE="${STATE_DIR}/pinned-on.env"
SCOPED_ON_FILE="${STATE_DIR}/scoped-on.env"
PROC_ROOT="${OMNITRADE_PROC_ROOT:-/proc}"

OFF_CONTENT='AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED=false
LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false
LIVE_CRYPTO_PREPARATION_ENABLED=true'
ON_CONTENT='AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED=true
LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false
LIVE_CRYPTO_PREPARATION_ENABLED=true'
DROPIN_CONTENT="[Service]
EnvironmentFile=${CURRENT_FILE}"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_root() {
  [[ "${EUID}" -eq 0 || "${OMNITRADE_ALLOW_NON_ROOT_FOR_TESTS:-}" == "1" ]] || \
    fail "${1} requires root; rerun with sudo"
}

assert_exact_file() {
  local path="$1" expected="$2"
  [[ -f "${path}" ]] || fail "required file is missing: ${path}"
  [[ "$(<"${path}")" == "${expected}" ]] || fail "unexpected content in ${path}; refusing to overwrite"
}

install_exact_file() {
  local path="$1" content="$2" mode="$3" temporary
  if [[ -e "${path}" ]]; then
    assert_exact_file "${path}" "${content}"
    return
  fi
  temporary="$(mktemp "${path}.tmp.XXXXXX")"
  printf '%s\n' "${content}" >"${temporary}"
  chmod "${mode}" "${temporary}"
  mv -f -- "${temporary}" "${path}"
}

select_file_atomically() {
  local source="$1" temporary
  temporary="$(mktemp "${CURRENT_FILE}.tmp.XXXXXX")"
  cp -- "${source}" "${temporary}"
  chmod 0644 "${temporary}"
  mv -f -- "${temporary}" "${CURRENT_FILE}"
}

write_file_atomically() {
  local path="$1" content="$2" temporary
  temporary="$(mktemp "${path}.tmp.XXXXXX")"
  printf '%s\n' "${content}" >"${temporary}"
  chmod 0644 "${temporary}"
  mv -f -- "${temporary}" "${path}"
}

process_value() {
  local pid="$1" name="$2"
  tr '\0' '\n' <"${PROC_ROOT}/${pid}/environ" | sed -n "s/^${name}=//p" | tail -n 1
}

verify_process_environment() {
  local expected_activation="$1" expected_package_id="${2:-}" expected_scope="${3:-}" pid activation submission preparation package_id scope
  "${SYSTEMCTL}" is-active --quiet "${UNIT}" || fail "${UNIT} is not active"
  pid="$("${SYSTEMCTL}" show "${UNIT}" --property=MainPID --value)"
  [[ "${pid}" =~ ^[1-9][0-9]*$ ]] || fail "${UNIT} has no valid MainPID"
  [[ -r "${PROC_ROOT}/${pid}/environ" ]] || fail "cannot read process environment for MainPID=${pid}"
  activation="$(process_value "${pid}" AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED)"
  submission="$(process_value "${pid}" LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED)"
  preparation="$(process_value "${pid}" LIVE_CRYPTO_PREPARATION_ENABLED)"
  package_id="$(process_value "${pid}" AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_PACKAGE_ID)"
  scope="$(process_value "${pid}" AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_CAMPAIGN_ID):$(process_value "${pid}" AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_CAMPAIGN_VERSION):$(process_value "${pid}" AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_MANDATE_ID):$(process_value "${pid}" AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_MANDATE_VERSION_ID)"
  [[ "${activation}" == "${expected_activation}" ]] || fail "activation flag mismatch: expected ${expected_activation}, got ${activation:-<missing>}"
  [[ "${submission}" == "false" ]] || fail "live submission is not explicitly false"
  [[ "${preparation}" == "true" ]] || fail "live preparation is not explicitly true"
  if [[ -n "${expected_package_id}" ]]; then
    [[ "${package_id}" == "${expected_package_id}" ]] || fail "activation package pin mismatch"
  elif [[ -n "${package_id}" ]]; then
    fail "unexpected activation package pin"
  fi
  [[ -z "${expected_scope}" || "${scope}" == "${expected_scope}" ]] || fail "activation canonical scope mismatch"
  printf 'unit=%s\nmain_pid=%s\nautomatic_activation=%s\nlive_submission=%s\nlive_preparation=%s\n' \
    "${UNIT}" "${pid}" "${activation}" "${submission}" "${preparation}"
  [[ -z "${package_id}" ]] || printf 'activation_package_id=%s\n' "${package_id}"
  [[ -z "${expected_scope}" ]] || printf 'activation_scope=%s\n' "${scope}"
}

restart_and_verify() {
  local expected_activation="$1" expected_package_id="${2:-}" expected_scope="${3:-}"
  "${SYSTEMCTL}" daemon-reload
  "${SYSTEMCTL}" restart "${UNIT}"
  verify_process_environment "${expected_activation}" "${expected_package_id}" "${expected_scope}"
}

verify_managed_files() {
  assert_exact_file "${DROPIN_FILE}" "${DROPIN_CONTENT}"
  assert_exact_file "${OFF_FILE}" "${OFF_CONTENT}"
  assert_exact_file "${ON_FILE}" "${ON_CONTENT}"
}

prepare() {
  require_root prepare
  mkdir -p -- "${DROPIN_DIR}" "${STATE_DIR}"
  install_exact_file "${OFF_FILE}" "${OFF_CONTENT}" 0644
  install_exact_file "${ON_FILE}" "${ON_CONTENT}" 0644
  install_exact_file "${DROPIN_FILE}" "${DROPIN_CONTENT}" 0644
  if [[ -e "${CURRENT_FILE}" ]] && \
    ! cmp -s -- "${CURRENT_FILE}" "${OFF_FILE}" && \
    ! cmp -s -- "${CURRENT_FILE}" "${ON_FILE}"; then
    fail "unexpected content in ${CURRENT_FILE}; refusing to overwrite"
  fi
  select_file_atomically "${OFF_FILE}"
  restart_and_verify false
  printf 'selector=PREPARED_OFF\n'
}

switch_on() {
  require_root on
  verify_managed_files
  select_file_atomically "${ON_FILE}"
  if ! (restart_and_verify true); then
    printf 'Activation verification failed; restoring the explicit OFF state.\n' >&2
    select_file_atomically "${OFF_FILE}"
    restart_and_verify false || fail "automatic rollback verification failed"
    fail "activation-only enablement failed and was rolled back"
  fi
  printf 'selector=ON\n'
}

switch_on_package() {
  local package_id="$1" content
  require_root on-package
  verify_managed_files
  [[ "${package_id}" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$ ]] || \
    fail "on-package requires a canonical UUID"
  content="${ON_CONTENT}
AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_PACKAGE_ID=${package_id}"
  write_file_atomically "${PINNED_ON_FILE}" "${content}"
  select_file_atomically "${PINNED_ON_FILE}"
  if ! (restart_and_verify true "${package_id}"); then
    printf 'Pinned activation verification failed; restoring the explicit OFF state.\n' >&2
    select_file_atomically "${OFF_FILE}"
    restart_and_verify false || fail "automatic rollback verification failed"
    fail "pinned activation-only enablement failed and was rolled back"
  fi
  printf 'selector=PINNED_ON\nactivation_package_id=%s\n' "${package_id}"
}

switch_on_scope() {
  local campaign_id="$1" campaign_version="$2" mandate_id="$3" mandate_version_id="$4" content scope
  require_root on-scope
  verify_managed_files
  for value in "${campaign_id}" "${mandate_id}" "${mandate_version_id}"; do
    [[ "${value}" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$ ]] || fail "on-scope requires canonical UUID identities"
  done
  [[ "${campaign_version}" =~ ^[1-9][0-9]*$ ]] || fail "on-scope requires a positive campaign version"
  content="${ON_CONTENT}
AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_CAMPAIGN_ID=${campaign_id}
AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_CAMPAIGN_VERSION=${campaign_version}
AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_MANDATE_ID=${mandate_id}
AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_MANDATE_VERSION_ID=${mandate_version_id}"
  scope="${campaign_id}:${campaign_version}:${mandate_id}:${mandate_version_id}"
  write_file_atomically "${SCOPED_ON_FILE}" "${content}"
  select_file_atomically "${SCOPED_ON_FILE}"
  if ! (restart_and_verify true "" "${scope}"); then
    printf 'Scoped activation verification failed; restoring the explicit OFF state.\n' >&2
    select_file_atomically "${OFF_FILE}"
    restart_and_verify false || fail "automatic rollback verification failed"
    fail "scoped activation-only enablement failed and was rolled back"
  fi
  printf 'selector=SCOPED_ON\nactivation_scope=%s\n' "${scope}"
}

switch_off() {
  require_root off
  verify_managed_files
  select_file_atomically "${OFF_FILE}"
  restart_and_verify false
  printf 'selector=OFF\n'
}

inspect() {
  verify_managed_files
  if cmp -s -- "${CURRENT_FILE}" "${OFF_FILE}"; then
    printf 'selector=OFF\n'
    verify_process_environment false
  elif cmp -s -- "${CURRENT_FILE}" "${ON_FILE}"; then
    printf 'selector=ON\n'
    verify_process_environment true
  elif [[ -f "${PINNED_ON_FILE}" ]] && cmp -s -- "${CURRENT_FILE}" "${PINNED_ON_FILE}"; then
    package_id="$(sed -n 's/^AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_PACKAGE_ID=//p' "${PINNED_ON_FILE}")"
    printf 'selector=PINNED_ON\n'
    verify_process_environment true "${package_id}"
  elif [[ -f "${SCOPED_ON_FILE}" ]] && cmp -s -- "${CURRENT_FILE}" "${SCOPED_ON_FILE}"; then
    campaign_id="$(sed -n 's/^AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_CAMPAIGN_ID=//p' "${SCOPED_ON_FILE}")"
    campaign_version="$(sed -n 's/^AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_CAMPAIGN_VERSION=//p' "${SCOPED_ON_FILE}")"
    mandate_id="$(sed -n 's/^AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_MANDATE_ID=//p' "${SCOPED_ON_FILE}")"
    mandate_version_id="$(sed -n 's/^AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_MANDATE_VERSION_ID=//p' "${SCOPED_ON_FILE}")"
    printf 'selector=SCOPED_ON\n'
    verify_process_environment true "" "${campaign_id}:${campaign_version}:${mandate_id}:${mandate_version_id}"
  else
    fail "current selector does not exactly match an approved state"
  fi
}

usage() {
  printf 'Usage: %s {prepare|on|on-package PACKAGE_ID|on-scope CAMPAIGN_ID CAMPAIGN_VERSION MANDATE_ID MANDATE_VERSION_ID|off|inspect}\n' "$0" >&2
  exit 2
}

case "$1" in
  on-package) [[ "$#" -eq 2 ]] || usage; switch_on_package "$2" ;;
  on-scope) [[ "$#" -eq 5 ]] || usage; switch_on_scope "$2" "$3" "$4" "$5" ;;
  prepare|on|off|inspect) [[ "$#" -eq 1 ]] || usage ;;
  *) usage ;;
esac
case "$1" in
  prepare) prepare ;;
  on) switch_on ;;
  on-package) ;;
  off) switch_off ;;
  inspect) inspect ;;
esac
