#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_PATH="${HEIR_OPENFHE_VENV:-${PROJECT_ROOT}/.venv-heir-python}"
HEIR_VERSION="${HEIR_VERSION:-2026.7.1}"

if [[ -z "${OPENFHE_PYTHON_VERSION:-}" ]]; then
  if [[ ! -r /etc/os-release ]]; then
    echo "Cannot detect the Linux distribution." >&2
    exit 2
  fi
  # shellcheck disable=SC1091
  source /etc/os-release
  case "${ID:-}:${VERSION_ID:-}" in
    ubuntu:24.04)
      OPENFHE_PYTHON_VERSION="1.5.1.0.24.4"
      ;;
    ubuntu:22.04)
      OPENFHE_PYTHON_VERSION="1.5.1.0.22.4"
      ;;
    *)
      echo "Unsupported OpenFHE wheel platform: ${ID:-unknown} ${VERSION_ID:-unknown}" >&2
      echo "Set OPENFHE_PYTHON_VERSION explicitly only after verifying a compatible wheel." >&2
      exit 2
      ;;
  esac
fi

"${PYTHON_BIN}" -c \
  'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ is required"'
mkdir -p "$(dirname "${VENV_PATH}")"
"${PYTHON_BIN}" -m venv "${VENV_PATH}"
"${VENV_PATH}/bin/python" -m pip install --upgrade pip
"${VENV_PATH}/bin/python" -m pip install \
  "heir_py[python,openfhe]==${HEIR_VERSION}" \
  "openfhe==${OPENFHE_PYTHON_VERSION}"

cd "${PROJECT_ROOT}"
"${VENV_PATH}/bin/python" \
  code/heir/scripts/check_heir_openfhe_python.py \
  --expected-heir "${HEIR_VERSION}" \
  --expected-openfhe "${OPENFHE_PYTHON_VERSION}"

echo
echo "Environment ready."
echo "Activate with: source ${VENV_PATH}/bin/activate"
echo "Do not add /usr/local/lib/OpenFHE to LD_LIBRARY_PATH in this environment."
