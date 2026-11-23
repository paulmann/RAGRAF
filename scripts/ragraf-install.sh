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
DIAGNOSE=0
FIRST_RUN=""
SERVICE_USER_ARG=""
CONFIG_PATH=""
REFRESH_MINUTES=15
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
    --diagnose) DIAGNOSE=1; shift ;;
    --first-run) FIRST_RUN="${2:-}"; shift 2 ;;
    --service-user) SERVICE_USER_ARG="${2:-}"; shift 2 ;;
    --config) CONFIG_PATH="${2:-}"; shift 2 ;;
    --refresh-minutes) REFRESH_MINUTES="${2:-15}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ОТКАЗ: неизвестный аргумент '$1'" >&2; exit 2 ;;
  esac
done

step() { printf '  [%s] %-22s %s\n' "$1" "$2" "$3"; }
fail() { printf '\nОТКАЗ: %s\n' "$2" >&2; step FAIL "$1" "$2"; exit "${3:-1}"; }

echo "=== RAGRAF: установка на Linux ==="

if [ "${DIAGNOSE}" -eq 1 ]; then
  # Диагностика ничего не меняет и не читает секретоносные пути: один блок для письма.
  echo
  echo "--- сведения об окружении (для поддержки) ---"
  echo "RAGRAF-установщик: ${SCRIPT_DIR}"
  echo "bash: $($BASH --version 2>/dev/null | head -1)"
  echo "ядро: $(uname -srm)"
  echo "учётная запись: $(id -un) (uid $(id -u))"
  echo
  echo "--- интерпретаторы ---"
  for candidate in $(command -v python3 python3.13 python3.14 python3.15 2>/dev/null) \
      /usr/bin/python3 /usr/local/bin/python3; do
    [ -x "${candidate}" ] || continue
    version="$("${candidate}" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || echo неизвестно)"
    if "${candidate}" -c 'import venv' >/dev/null 2>&1; then venv_ok="есть"; else venv_ok="НЕТ"; fi
    echo "  · ${candidate} — версия ${version}, venv: ${venv_ok}"
  done
  echo
  echo "--- установка, если уже есть ---"
  target="${PREFIX:-${HOME}/.local/share/ragraf}"
  if [ -f "${target}/src/ragraf/__init__.py" ]; then
    echo "каталог: ${target}"
    echo "версия: $(installed_version 2>/dev/null || sed -n 's/^__version__ *= *"\(.*\)"/\1/p' "${target}/src/ragraf/__init__.py" | head -1)"
    if [ -x "${target}/.venv/bin/ragraf" ]; then
      echo "ragraf --version: $("${target}/.venv/bin/ragraf" --version 2>&1 | head -1)"
      if [ -f "${target}/config/ragraf.yaml" ]; then
        "${target}/.venv/bin/ragraf" -p "${target}/config/ragraf.yaml" status 2>&1 | head -6 | sed 's/^/  /'
      else
        echo "  · своего конфига нет (установка без корпуса)"
      fi
    else
      echo "  · .venv/bin/ragraf нет — зависимости не встали"
    fi
  else
    echo "  · не установлено в ${target}"
  fi
  echo
  echo "--- свободное место ---"
  df -h "${HOME}" 2>/dev/null | tail -1 | sed 's/^/  /'
  echo
  echo "Конец блока диагностики."
  exit 0
fi

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
# Умолчание конфигурации для службы: конфиг, который лежит в поставке. Если его нет —
# служба не создаётся (иначе ExecStart падает с 203/EXEC на несуществующем пути).
if [ -z "${CONFIG_PATH}" ]; then
  CONFIG_PATH="${PREFIX}/config/ragraf.yaml"
fi

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
MARKER="${PREFIX}/.ragraf/install.json"
if [ -f "${MARKER}" ]; then
  # Маркер пишет наш же установщик. Другой путь в нём — это чужое состояние,
  # и перезаписывать его нельзя: там лицензия и реестр другой установки.
  MARKED="$(sed -n 's/.*"install_path": *"\([^"]*\)".*/\1/p' "${MARKER}" | head -1)"
  if [ -n "${MARKED}" ] && [ "${MARKED}" != "${PREFIX}" ]; then
    fail Каталог "состояние в ${PREFIX}/.ragraf принадлежит установке ${MARKED}: выберите другой каталог" 2
  fi
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
      if [ -L "${link}" ]; then
        if [ -e "${link}" ]; then
          rm -f -- "${link}"
          step ok PATH "симлинк ${link} убран"
        else
          rm -f -- "${link}"
          step ok PATH "битый симлинк ${link} убран"
        fi
      fi
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
STATE_NAMES="catalog .ragraf edition.license edition.license.sig edition.pub"

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
  # Новый каталог собирается рядом и подменяет старый двумя переименованиями:
  # так окно недоступности измеряется миллисекундами, а не секундами (замер 23.11.2026:
  # 2 678 мс и 5 из 5 неудачных параллельных обращений при переносе «уносим и ставим»).
  NEW="${PREFIX}.new-$$"
  rm -rf -- "${NEW}"
  mkdir -p -- "${NEW}"
  ( cd -- "${SOURCE}" && tar -cf - \
      --exclude='./.git' --exclude='./.ragraf' --exclude='./.venv' \
      --exclude='./.work' --exclude='./.zwork' --exclude='./.staging' \
      --exclude='./.tools' --exclude='./node_modules' --exclude='*/__pycache__' \
      --exclude='./.pytest_cache' --exclude='./.mypy_cache' . ) |
    ( cd -- "${NEW}" && tar -xf - )
  COPIED="$(sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p' "${NEW}/src/ragraf/__init__.py" | head -1)"
  [ "${COPIED}" = "${VERSION}" ] || { rm -rf -- "${NEW}"; fail Распаковка "в новом каталоге версия ${COPIED} вместо ${VERSION}"; }

  if [ -n "${INSTALLED}" ]; then
    # Состояние переезжает в НОВЫЙ каталог: реестр, хранилище секретов, индекс, лицензия
    # и конфиги корпусов. Окружение — только если его ставили.
    MOVED=""
    for name in ${STATE_NAMES}; do
      if [ -e "${PREFIX}/${name}" ]; then
        rm -rf -- "${NEW:?}/${name}"
        mv -- "${PREFIX}/${name}" "${NEW}/"
        MOVED="${MOVED} ${name}"
      fi
    done
    if [ -d "${PREFIX}/config" ]; then
      # Конфиги корпусов, созданные ensure/init, живут в config/ установки: новый
      # набор выигрывает по совпадающим именам, остальные переносятся как есть.
      # Каталог создаётся ДО копирования: в поставке его может не быть вовсе
      # (измерено 23.11.2026: cp падал «Нет такого файла или каталога», и обновление
      # обрывалось, оставляя маркер состояния в осиротевшем .new-каталоге).
      mkdir -p -- "${NEW}/config"
      for cfg_file in "${PREFIX}/config/"*; do
        [ -e "${cfg_file}" ] || continue
        base="$(basename -- "${cfg_file}")"
        [ -e "${NEW}/config/${base}" ] || cp -a -- "${cfg_file}" "${NEW}/config/${base}"
      done
      MOVED="${MOVED} config"
    fi
    # Окружение НЕ уносим до подмены: пока идёт сборка, установка обязана работать
    # (замер 23.11.2026: унесли на начале сборки — 88 отказов из 95 обращений).
    if [ -n "${MOVED}" ]; then step ok Состояние "перенесено:${MOVED}"; fi

    BACKUP="${PREFIX}.bak-$(date +%Y%m%d-%H%M%S)"
    mv -- "${PREFIX}" "${BACKUP}" || { rm -rf -- "${NEW}"; fail Распаковка "не удалось отложить прежнюю установку"; }
    mv -- "${NEW}" "${PREFIX}" || { mv -- "${BACKUP}" "${PREFIX}"; fail Распаковка "подмена не удалась, прежняя установка возвращена"; }
    step ok Прежняя версия "${INSTALLED} → ${BACKUP}"
    if [ "${SKIP_DEPS}" -eq 0 ] && [ -d "${BACKUP}/.venv" ]; then
      # Третье переименование на том же томе: пути внутри venv не меняются, потому
      # что каталог прежний, — перепривязка editable-пакета не нужна.
      mv -- "${BACKUP}/.venv" "${PREFIX}/.venv" || \
        step warn Окружение "прежнее окружение не переехало: соберите его запуском без --skip-deps"
      MOVED="${MOVED} .venv"
    fi
  else
    rm -rf -- "${PREFIX}"
    mv -- "${NEW}" "${PREFIX}"
  fi
  step ok Распаковка "версия ${COPIED}"

fi

# --- 6. окружение ---------------------------------------------------------
if [ -x "${PREFIX}/.venv/bin/ragraf" ] && [ "${SKIP_DEPS}" -eq 0 ] && [ -n "${BACKUP:-}" ]; then
  # Перепривязка уже сделана в новом каталоге ДО подмены (шаг 5): здесь только слово.
  step ok Окружение "перенесено из прежней установки (перепривязано)"
elif [ "${SKIP_DEPS}" -eq 0 ]; then
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
  # Свойство: симлинк либо отсутствует, либо указывает на существующий файл.
  # Битый симлинк опаснее отсутствующего: оболочка находит команду и отвергает
  # её выполнение (измерено 23.11.2026 при ручном удалении каталога).
  if [ ! -e "${LINK_DIR}/ragraf" ]; then
    fail PATH "симлинк ${LINK_DIR}/ragraf создан, но цель ${BIN} недоступна" 1
  fi
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
  elif [ ! -f "${CONFIG_PATH}" ]; then
    # Юнит без конфигурации — служба, падающая на старте. Отказ, а не запись
    # «на всякий случай» (измерено: status=203/EXEC).
    fail Служба "нет файла конфигурации ${CONFIG_PATH}: укажите --config <файл>" 2
  else
    SERVICE_USER="${SERVICE_USER_ARG:-ragraf}"
    # Интерпретатор из домашнего каталога служебному пользователю недоступен: shebang
    # venv указывает туда, а /home/<кто-то> обычно 700 — systemd даёт 203/EXEC
    # (измерено 23.11.2026 на 192.168.31.31: venv собран ~/.local/python313).
    case "${PYTHON_BIN}" in
      /home/*|/root/*)
        OWNER_OF_PY="$(printf '%s' "${PYTHON_BIN}" | cut -d/ -f3)"
        if [ "${OWNER_OF_PY}" != "${SERVICE_USER}" ]; then
          fail Служба "интерпретатор ${PYTHON_BIN} лежит в домашнем каталоге ${OWNER_OF_PY}, а служба работает от ${SERVICE_USER}: нужен системный Python 3.13 или --service-user ${OWNER_OF_PY}" 3
        fi
        ;;
    esac
    if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
      useradd --system --no-create-home --shell /usr/sbin/nologin "${SERVICE_USER}" \
        || fail Служба "не удалось создать пользователя ${SERVICE_USER}" 1
      step ok Служба "пользователь ${SERVICE_USER} создан"
    fi
    # Файлы должны быть читаемы и исполняемы службой: без этого systemd даёт
    # 203/EXEC «Permission denied» (измерено с User=nobody).
    chown -R "${SERVICE_USER}:${SERVICE_USER}" "${PREFIX}" 2>/dev/null || true
    chmod -R a+rX "${PREFIX}"
    chown "${SERVICE_USER}:${SERVICE_USER}" "${CONFIG_PATH}" 2>/dev/null || true
    # Обновление индекса по таймеру, а не «демон сторожа»: подкоманды watch в CLI нет,
    # а свежесть проверяется внутри каждого вызова фасада. Таймер запускает то, что
    # действительно существует (check), и называется по делу.
    UNIT=/etc/systemd/system/ragraf-refresh.service
    TIMER=/etc/systemd/system/ragraf-refresh.timer
    cat > "${UNIT}" <<EOF
[Unit]
Description=RAGRAF: проверить свежесть корпуса и пересобрать при расхождении
After=network.target

[Service]
Type=oneshot
User=${SERVICE_USER}
Group=${SERVICE_USER}
ExecStart=${BIN} -p ${CONFIG_PATH} check
TimeoutStartSec=1800
EOF
    cat > "${TIMER}" <<EOF
[Unit]
Description=RAGRAF: периодическая проверка свежести (${REFRESH_MINUTES} мин)

[Timer]
OnBootSec=5min
OnUnitActiveSec=${REFRESH_MINUTES}min
Unit=ragraf-refresh.service

[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload
    step ok Служба "${TIMER} (каждые ${REFRESH_MINUTES} мин, пользователь ${SERVICE_USER})"
  fi
fi

# --- 10. первый запуск ------------------------------------------------------
if [ -n "${FIRST_RUN}" ]; then
  if [ ! -d "${FIRST_RUN}" ]; then
    step FAIL "Первый запуск" "каталога ${FIRST_RUN} нет"
  elif [ ! -x "${BIN}" ]; then
    step skip "Первый запуск" "без окружения первый запуск невозможен"
  else
    ENSURE="$("${BIN}" ensure "${FIRST_RUN}" --json 2>&1)"
    STATUS="$(printf '%s' "${ENSURE}" | sed -n 's/.*"status": *"\([^"]*\)".*/\1/p' | head -1)"
    case "${STATUS}" in
      actual|reindexed|created) step ok "Первый запуск ensure" "статус ${STATUS}" ;;
      *) step FAIL "Первый запуск ensure" "статус «${STATUS}»" ;;
    esac
    CFG_FIRST="$(printf '%s' "${ENSURE}" | sed -n 's/.*"config": *"\([^"]*\)".*/\1/p' | head -1)"
    if [ -n "${CFG_FIRST}" ]; then
      LINE="$("${BIN}" -p "${CFG_FIRST}" status 2>&1 | grep -m1 'capability=' || true)"
      [ -n "${LINE}" ] && step ok "Первый запуск status" "${LINE}"
      case "${LINE}" in
        *capability=full*) : ;;
        *capability=*)
          step warn Возможности "${LINE}: Joern или модель эмбеддингов не настроены —
честное состояние, а не ошибка" ;;
      esac
      REFS="$("${BIN}" -p "${CFG_FIRST}" query search 'def ' --limit 3 2>&1 | grep -cE ':[0-9]+' || true)"
      if [ "${REFS:-0}" -gt 0 ]; then
        step ok "Первый запуск поиск" "адресов файл:строка ${REFS}"
      else
        step skip "Первый запуск поиск" "поиск не дал адресов (маленький корпус)"
      fi
    fi
  fi
fi

# --- маркер установки -----------------------------------------------------
# Свой маркер: по нему другой запуск увидит, что состояние принадлежит иной
# установке. Догадаться об этом по данным .ragraf нельзя — поля «чей индекс» там нет.
mkdir -p -- "${PREFIX}/.ragraf"
printf '{\n  "installer": "ragraf-install.sh",\n  "install_path": "%s",\n  "version": "%s",\n  "written_at": "%s"\n}\n' \
  "${PREFIX}" "${VERSION}" "$(date -Iseconds)" > "${PREFIX}/.ragraf/install.json"

echo
echo "Готово: RAGRAF ${VERSION} в ${PREFIX}"
echo "Что осталось настроить по желанию:"
echo "  · Joern для срезов свойств кода: scripts/install-joern.ps1 (без него capability=degraded)"
echo "  · платное издание: положите edition.license, edition.license.sig и edition.pub рядом с catalog/"
