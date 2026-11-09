#!/usr/bin/env bash
# Установка или обновление Telegram-бота «Ассистент по Instagram» на сервере Ubuntu/Debian.
# Запускать от root:  sudo bash deploy/install.sh
# Повторный запуск безопасен: обновит код и зависимости, перезапустит службу.
#
# Бот ставится в отдельную папку /opt/insta-assistant, чтобы не мешать боту «Совещание».

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/arkahakick21-del/open-sites.git}"
BRANCH="${BRANCH:-main}"
APP_DIR="/opt/insta-assistant"
BOT_DIR="$APP_DIR/instagram-bot"
SERVICE="insta-bot"
RUN_USER="insta"

if [[ $EUID -ne 0 ]]; then
  echo "Запустите от root: sudo bash $0" >&2
  exit 1
fi

echo "1/7 Пакеты системы"
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip ffmpeg fontconfig >/dev/null

echo "2/7 Шрифты для субтитров"
for pkg in fonts-dejavu-core fonts-noto-core fonts-roboto fonts-open-sans fonts-montserrat fonts-inter; do
  apt-get install -y -qq "$pkg" >/dev/null 2>&1 || echo "   пакет $pkg недоступен, пропускаю"
done
fc-cache -f >/dev/null

echo "3/7 Пользователь $RUN_USER"
if ! id "$RUN_USER" &>/dev/null; then
  useradd --system --create-home --home-dir /var/lib/$RUN_USER --shell /usr/sbin/nologin "$RUN_USER"
fi

echo "4/7 Код в $APP_DIR"
if [[ -d "$APP_DIR/.git" ]]; then
  git config --global --add safe.directory "$APP_DIR"
  git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$APP_DIR" checkout -q -B "$BRANCH" FETCH_HEAD
else
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

echo "5/7 Виртуальное окружение и зависимости (первый раз несколько минут)"
if [[ ! -d "$BOT_DIR/.venv" ]]; then
  python3 -m venv "$BOT_DIR/.venv"
fi
"$BOT_DIR/.venv/bin/pip" install -q --upgrade pip
"$BOT_DIR/.venv/bin/pip" install -q -r "$BOT_DIR/requirements.txt"

echo "6/7 Файл .env"
if [[ ! -f "$BOT_DIR/.env" ]]; then
  cp "$BOT_DIR/.env.example" "$BOT_DIR/.env"
  echo
  echo "   Создан $BOT_DIR/.env. Впишите в него ключи:"
  echo "   nano $BOT_DIR/.env"
  echo "   Потом запустите этот скрипт ещё раз."
  NEED_ENV=1
fi
mkdir -p "$BOT_DIR/data"
chown -R "$RUN_USER:$RUN_USER" "$BOT_DIR"
chmod 600 "$BOT_DIR/.env"

echo "7/7 Служба systemd"
cp "$BOT_DIR/deploy/$SERVICE.service" /etc/systemd/system/$SERVICE.service
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null

if [[ "${NEED_ENV:-0}" == "1" ]]; then
  echo
  echo "Служба установлена, но не запущена: сначала заполните .env."
  exit 0
fi

systemctl restart "$SERVICE"
sleep 3
if systemctl is-active --quiet "$SERVICE"; then
  echo
  echo "Готово. Бот работает."
  echo "Логи:        journalctl -u $SERVICE -f"
  echo "Перезапуск:  systemctl restart $SERVICE"
  echo "Остановка:   systemctl stop $SERVICE"
else
  echo
  echo "Служба не поднялась. Смотрите причину:"
  echo "journalctl -u $SERVICE -n 50 --no-pager"
  exit 1
fi
