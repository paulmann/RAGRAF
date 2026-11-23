#!/usr/bin/env bash
# Установка RAGRAF на Linux (проверено на Debian 12).
#
# Тонкая обёртка над существующими установщиками: зависимости ставит
# `scripts/install.ps1` (через pwsh) — но на Linux pwsh может отсутствовать, поэтому
# здесь окружение ставится напрямую: python3 -m venv + pip install из поставки.
#
# Режимы:
#   --prefix <dir>   каталог установки (по умолчанию ~/.local/share/ragraf)
#   --system         системная установка в /opt/ragraf (нужен root)
#   --upgrade        перенести прежнюю установку в <prefix>.bak-<метка>
#   --dry-run        показать план, ничего не менять
#   --uninstall      убрать программу, сохранив каталог состояния
#   --purge          убрать вместе с состоянием
#   --no-path        не трогать PATH (симлинк не создаётся)
#   --skip-deps      не ставить зависимости
#   --skip-verify    не запускать проверки
#   --service        поставить systemd unit сторожа (только с --system и root)
#
# Коды: 0 — установлено, 1 — ошибка, 2 — аргументы, 3 — окружение.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="${SCRIPT_DIR}"
if [ ! -f "${SOURCE}/src/ragraf/__init__.py" ]; then
  SOURCE="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
fi

PREFIX=""
SYSTEM=0
UPGRADE=0
DRY_RUN=0
UNINSTALL=0
PURGE=0
NO_PATH=0
SKIP_DEPS=0
SKIP_VERIFY=0
SERVICE=0
PYTHON_BIN="${RAGRAF_PYTHON:-}"

usage() {
  sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="${2:-}"; shift 2 ;;
    --system) SYSTEM=1; shift ;;
    --upgrade) UPGRADE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --purge) PURGE=1; shift ;;
    --no-path) NO_PATH=1; shift ;;
    --skip-deps) SKIP_DEPS=1; shift ;;
    --skip-verify) SKIP_VERIFY=1; shift ;;
    --service) SERVICE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ОТКАЗ: неизвестный аргумент '$1'" >&2; exit 2 ;;
  esac
done

step() { printf '  [%s] %-22s %s\n' "$1" "$2" "$3"; }
fail() { printf '\nОТКАЗ: %s\n' "$2" >&2; step FAIL "$1" "$2"; exit "${3:-1}"; }

echo "=== RAGRAF: установка на Linux ==="

# --- 1. каталог: дешёвые отказы раньше окружения -------------------------
# Аргументные ошибки (плохой каталог, --system без root) называются ДО поиска
# интерпретатора: иначе на машине без Python 3.13 человек слышал бы про Python, а
# причина была в каталоге (измерено 23.11.2026 на Debian 12).
if [ "${SYSTEM}" -eq 1 ] && [ -z "${PREFIX}" ]; then
  PREFIX="/opt/ragraf"
fi
if [ -z "${PREFIX}" ]; then
  PREFIX="${HOME}/.local/share/ragraf"
fi
PREFIX="${PREFIX%/}"

case "${PREFIX}" in
  /|/usr|/usr/local|/etc|/var|/var/www|/var/www/*|/opt)
    fail Каталог "каталог ${PREFIX} — системный корень или чужой каталог; выберите подкаталог (например /opt/ragraf)" 2 ;;
  /opt/*)
    # /opt — место системной установки: подкаталог разрешён только с --system,
    # иначе это чужая машина с чужими каталогами (измерено 23.11.2026: без этой
    # развилки --system получал отказ про каталог вместо отказа про root).
    [ "${SYSTEM}" -eq 1 ] || fail Каталог "каталог ${PREFIX} под /opt ставится только с флагом --system" 2 ;;
esac
if [ "${SYSTEM}" -eq 1 ] && [ "$(id -u)" -ne 0 ]; then
  fail Каталог "--system требует root; запустите через sudo или уберите --system" 3
fi
if [ -e "${PREFIX}/.ragraf" ] && [ ! -f "${PREFIX}/src/ragraf/__init__.py" ]; then
  fail Каталог "каталог ${PREFIX} уже содержит индекс .ragraf: установка перезапишет чужой корпус" 2
fi
if [ "${SYSTEM}" -ne 1 ] && [ "$(id -u)" -eq 0 ]; then
  step warn Пользователь "запуск от root без --system: ставлю в ${PREFIX}; для системной установки добавьте --system"
fi
step ok Каталог "${PREFIX}"

# --- 2. удаление (до окружения: ему Python не нужен) ----------------------
if [ "${UNINSTALL}" -eq 1 ]; then
  [ -d "${PREFIX}" ] || fail Удаление "каталога ${PREFIX} нет"
  if [ "${DRY_RUN}" -eq 1 ]; then
    step skip Удаление "план: убрать ${PREFIX}$( [ "${PURGE}" -eq 0 ] && echo ', состояние сохранить')"
    exit 0
  fi
  [ "${PURGE}" -eq 1 ] || stash_state
  rm -rf -- "${PREFIX}"
  step ok Удаление "${PREFIX} удалён$( [ "${PURGE}" -eq 1 ] && echo ' с состоянием')"
  if [ "${NO_PATH}" -eq 0 ]; then
    for link in "${HOME}/.local/bin/ragraf" "/usr/local/bin/ragraf"; do
      if [ -L "${link}" ]; then rm -f -- "${link}"; step ok PATH "симлинк ${link} убран"; fi
    done
  fi
  exit 0
fi

# --- 3. интерпретатор -----------------------------------------------------
if [ -z "${PYTHON_BIN}" ]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi
[ -n "${PYTHON_BIN}" ] || fail Python "python3 не найден в PATH; поставьте Python 3.13+ (инсталлятор его не тянет из сети)" 3
[ -x "${PYTHON_BIN}" ] || fail Python "указанный интерпретатор ${PYTHON_BIN} не исполняемый" 3

VER="$("${PYTHON_BIN}" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo none)"
case "${VER}" in
  3.1[3-9]|3.[2-9][0-9]) : ;;
  *) fail Python "нужен Python 3.13+, найден ${VER} (${PYTHON_BIN}); на Debian 12 системный 3.11 — нужен 3.13 из backports или собранный" 3 ;;
esac
"${PYTHON_BIN}" -c 'import venv' 2>/dev/null || fail Python "у интерпретатора ${PYTHON_BIN} нет модуля venv (нужен пакет python3-venv)" 3
step ok Python "${VER} (${PYTHON_BIN})"

# --- 4. источник ---------------------------------------------------------
VERSION="$(sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p' "${SOURCE}/src/ragraf/__init__.py" | head -1)"
[ -n "${VERSION}" ] || fail Поставка "в ${SOURCE} нет src/ragraf/__init__.py — это не дерево RAGRAF"
if [ -n "${RAGRAF_EXPECTED_VERSION:-}" ] && [ "${RAGRAF_EXPECTED_VERSION}" != "${VERSION}" ]; then
  fail Поставка "версия дерева ${VERSION} не совпадает с заявленной ${RAGRAF_EXPECTED_VERSION}"
fi
step ok Поставка "версия ${VERSION}, ${SOURCE}"

installed_version() {
  if [ -f "${PREFIX}/src/ragraf/__init__.py" ]; then
    sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p' "${PREFIX}/src/ragraf/__init__.py" | head -1
  fi
}
INSTALLED="$(installed_version)"

stash_state() {
  # Состояние (catalog/) переносится в сторону, а не удаляется: там лицензия и реестр.
  local stash
  stash="$(mktemp -d "${TMPDIR:-/tmp}/ragraf-state-XXXXXX")"
  local moved=0
  # Один каталог состояния; цикл оставлен ради будущих (.ragraf, logs) — shellcheck
  # справедливо ругается на цикл из одного элемента, поэтому список явный.
  if [ -d "${PREFIX}/catalog" ]; then
    mv "${PREFIX}/catalog" "${stash}/"
    moved=1
  fi
  if [ "${moved}" -eq 1 ]; then
    step ok Состояние "сохранено в ${stash}"
  fi
}

# --- 5. та же версия ------------------------------------------------------
SAME=0
if [ -n "${INSTALLED}" ] && [ "${INSTALLED}" = "${VERSION}" ]; then SAME=1; fi

if [ "${DRY_RUN}" -eq 1 ]; then
  if [ "${SAME}" -eq 1 ] && [ "${UPGRADE}" -eq 0 ]; then
    step skip Установка "уже установлено: версия ${INSTALLED}"
  else
    step skip Распаковка "план: ${SOURCE} → ${PREFIX}"
    step skip Окружение "план: ${PREFIX}/.venv через ${PYTHON_BIN}"
    step skip PATH "план: симлинк ragraf в ${HOME}/.local/bin"
  fi
  exit 0
fi

if [ "${SAME}" -eq 1 ] && [ "${UPGRADE}" -eq 0 ]; then
  step skip Установка "уже установлено: версия ${INSTALLED} в ${PREFIX}"
else
  if [ -n "${INSTALLED}" ]; then
    BACKUP="${PREFIX}.bak-$(date +%Y%m%d-%H%M%S)"
    mv -- "${PREFIX}" "${BACKUP}"
    step ok Прежняя версия "${INSTALLED} → ${BACKUP}"
  fi
  mkdir -p -- "${PREFIX}"
  # Копируется содержимое поставки; .venv прежней установки уехал в бэкап вместе с ней.
  ( cd -- "${SOURCE}" && tar -cf - --exclude='./.git' --exclude='./.ragraf' . ) | ( cd -- "${PREFIX}" && tar -xf - )
  COPIED="$(installed_version)"
  [ "${COPIED}" = "${VERSION}" ] || fail Распаковка "после копирования версия ${COPIED} вместо ${VERSION}"
  step ok Распаковка "версия ${COPIED}"
fi

# --- 6. окружение ---------------------------------------------------------
if [ "${SKIP_DEPS}" -eq 0 ]; then
  VENV="${PREFIX}/.venv"
  if [ ! -x "${VENV}/bin/python" ]; then
    "${PYTHON_BIN}" -m venv "${VENV}" || fail Окружение "не удалось создать ${VENV}"
  fi
  "${VENV}/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
  # PyNaCl обязателен: без него хранилище секретов и лицензия помечаются unverifiable
  # и издание молча понижается до community (AGENTS.md, раздел 6).
  ( cd -- "${PREFIX}" && "${VENV}/bin/python" -m pip install --quiet -e ".[secrets]" ) \
    || fail Окружение "pip install -e .[secrets] не удался (нет сети или wheel'ов для этой платформы)"
  step ok Окружение "${VENV}"
elif [ "${SAME}" -eq 0 ]; then
  step skip Окружение "пропущено по --skip-deps"
fi

# --- 7. PATH --------------------------------------------------------------
BIN="${PREFIX}/.venv/bin/ragraf"
if [ "${NO_PATH}" -eq 0 ]; then
  if [ "${SYSTEM}" -eq 1 ]; then
    LINK_DIR="/usr/local/bin"
  else
    LINK_DIR="${HOME}/.local/bin"
  fi
  mkdir -p -- "${LINK_DIR}"
  ln -sfn -- "${BIN}" "${LINK_DIR}/ragraf"
  step ok PATH "симлинк ${LINK_DIR}/ragraf"
  case ":${PATH}:" in
    *":${LINK_DIR}:"*) : ;;
    *) step warn PATH "${LINK_DIR} не в PATH этого сеанса; добавьте его в профиль" ;;
  esac
else
  step skip PATH "не трогали по --no-path"
fi

# --- 8. проверки ----------------------------------------------------------
if [ "${SKIP_VERIFY}" -eq 0 ]; then
  [ -x "${BIN}" ] || fail Проверка "после установки нет ${BIN} — зависимости не поставились"
  OUT="$("${BIN}" --version 2>&1 | head -1)"
  case "${OUT}" in
    *"${VERSION}"*) step ok Проверка "${OUT}" ;;
    *) fail Проверка "ragraf --version вернул «${OUT}» (ожидалась ${VERSION})" ;;
  esac

  PROBE="$(mktemp -d "${TMPDIR:-/tmp}/ragraf-probe-XXXXXX")"
  trap 'rm -rf -- "${PROBE}"' EXIT
  printf '.ragraf/\n' > "${PROBE}/.gitignore"
  # ensure требует минимум три файла с кодом и маркер проекта (src/ragraf/ensure.py:478).
  for name in main util model; do
    printf 'def %s():\n    return 1\n' "${name}" > "${PROBE}/${name}.py"
  done
  if [ "$(id -u)" -eq 0 ]; then
    chown -R --reference="${PROBE}" "${PROBE}" 2>/dev/null || true
  fi
  ENSURE="$("${BIN}" ensure "${PROBE}" --json 2>&1)"
  STATUS="$(printf '%s' "${ENSURE}" | sed -n 's/.*"status": *"\([^"]*\)".*/\1/p' | head -1)"
  case "${STATUS}" in
    actual|reindexed|created) step ok "Проверка ensure" "статус ${STATUS} на пробном проекте" ;;
    *) fail "Проверка ensure" "ensure вернул статус «${STATUS}»" ;;
  esac
  CFG="$(printf '%s' "${ENSURE}" | sed -n 's/.*"config": *"\([^"]*\)".*/\1/p' | head -1)"
  if [ -n "${CFG}" ]; then
    if "${BIN}" -p "${CFG}" check >/dev/null 2>&1; then
      step ok "Проверка check" "корпус пробы сверен"
    else
      fail "Проверка check" "check пробного корпуса вернул ненулевой код"
    fi
  else
    step skip "Проверка check" "ensure не назвал конфиг корпуса"
  fi

  FP="$("${BIN}" edition --fingerprint 2>&1 | grep -oE '[0-9a-f]{64}' | head -1 || true)"
  if [ -n "${FP}" ]; then step ok Отпечаток "${FP}"; else step skip Отпечаток "edition --fingerprint не назвал отпечаток"; fi
else
  step skip Проверка "пропущено по --skip-verify"
fi

# --- 9. служба сторожа (только --system и root) ---------------------------
if [ "${SERVICE}" -eq 1 ]; then
  if [ "${SYSTEM}" -ne 1 ] || [ "$(id -u)" -ne 0 ]; then
    step skip Служба "--service требует --system и root"
  elif ! command -v systemctl >/dev/null 2>&1; then
    step skip Служба "systemctl не найден (не systemd)"
  else
    UNIT=/etc/systemd/system/ragraf-watch.service
    cat > "${UNIT}" <<EOF
[Unit]
Description=RAGRAF watchdog (refresh stale corpora)
After=network.target

[Service]
Type=simple
ExecStart=${BIN} -p /etc/ragraf/ragraf.yaml watch
Restart=on-failure
RestartSec=30
User=nobody
Group=nogroup

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    step ok Служба "${UNIT} создан (запуск: systemctl enable --now ragraf-watch)"
  fi
fi

echo
echo "Готово: RAGRAF ${VERSION} в ${PREFIX}"
echo "Что осталось настроить по желанию:"
echo "  · Joern для срезов свойств кода: scripts/install-joern.ps1 (без него capability=degraded)"
echo "  · платное издание: положите edition.license, edition.license.sig и edition.pub рядом с catalog/"
