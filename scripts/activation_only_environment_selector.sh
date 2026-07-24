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

process_value() {
  local pid="$1" name="$2"
  tr '\0' '\n' <"${PROC_ROOT}/${pid}/environ" | sed -n "s/^${name}=//p" | tail -n 1
}

verify_process_environment() {
  local expected_activation="$1" pid activation submission preparation
  "${SYSTEMCTL}" is-active --quiet "${UNIT}" || fail "${UNIT} is not active"
  pid="$("${SYSTEMCTL}" show "${UNIT}" --property=MainPID --value)"
  [[ "${pid}" =~ ^[1-9][0-9]*$ ]] || fail "${UNIT} has no valid MainPID"
  [[ -r "${PROC_ROOT}/${pid}/environ" ]] || fail "cannot read process environment for MainPID=${pid}"
  activation="$(process_value "${pid}" AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED)"
  submission="$(process_value "${pid}" LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED)"
  preparation="$(process_value "${pid}" LIVE_CRYPTO_PREPARATION_ENABLED)"
  [[ "${activation}" == "${expected_activation}" ]] || fail "activation flag mismatch: expected ${expected_activation}, got ${activation:-<missing>}"
  [[ "${submission}" == "false" ]] || fail "live submission is not explicitly false"
  [[ "${preparation}" == "true" ]] || fail "live preparation is not explicitly true"
  printf 'unit=%s\nmain_pid=%s\nautomatic_activation=%s\nlive_submission=%s\nlive_preparation=%s\n' \
    "${UNIT}" "${pid}" "${activation}" "${submission}" "${preparation}"
}

restart_and_verify() {
  local expected_activation="$1"
  "${SYSTEMCTL}" daemon-reload
  "${SYSTEMCTL}" restart "${UNIT}"
  verify_process_environment "${expected_activation}"
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
  else
    fail "current selector does not exactly match an approved state"
  fi
}

usage() {
  printf 'Usage: %s {prepare|on|off|inspect}\n' "$0" >&2
  exit 2
}

[[ "$#" -eq 1 ]] || usage
case "$1" in
  prepare) prepare ;;
  on) switch_on ;;
  off) switch_off ;;
  inspect) inspect ;;
  *) usage ;;
esac
