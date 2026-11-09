# Как развернуть ассистента на облачном сервере

Пошагово, для человека без опыта с серверами. Займёт 30–40 минут.

## Что понадобится

- Ключ Anthropic: https://console.anthropic.com/settings/keys
- Ключ OpenAI для субтитров и голосовых: https://platform.openai.com/api-keys
- Аккаунт Telegram у вас и у специалиста
- Банковская карта для аренды сервера

## Важно про страну сервера

API Anthropic и OpenAI не обслуживают запросы с российских IP-адресов. Сервер должен стоять
за границей: Нидерланды, Германия, Польша, Казахстан и так далее.

## Шаг 1. Создать бота в Telegram

1. Откройте `@BotFather` и отправьте `/newbot`.
2. Название, например `Ассистент Instagram`.
3. Имя пользователя должно заканчиваться на `bot`, например `insta_helper_anna_bot`.
4. BotFather пришлёт токен вида `1234567890:AAH...`. Никому его не показывайте.

## Шаг 2. Арендовать сервер

Монтаж видео требует больше ресурсов, чем обычный бот:

- 2 процессора, 4 ГБ памяти, от 40 ГБ диска;
- система **Ubuntu 24.04**;
- локация **не в России**.

Подойдут Timeweb Cloud, Aeza, VDSina с оплатой в рублях или Hetzner с оплатой в евро.
Такой тариф стоит примерно 700–1500 ₽ в месяц.

Если на этом сервере уже работает бот «Совещание», ставьте рядом: боты друг другу не мешают.

## Шаг 3. Подключиться к серверу

```bash
ssh root@123.45.67.89
```

На вопрос `Are you sure you want to continue connecting?` ответьте `yes` и введите пароль root.

## Шаг 4. Установить бота

```bash
apt-get update && apt-get install -y git
```

```bash
git clone --depth 1 https://github.com/arkahakick21-del/open-sites.git /tmp/os && bash /tmp/os/instagram-bot/deploy/install.sh
```

Пока изменения не влиты в основную ветку, укажите ветку явно:

```bash
git clone --depth 1 -b claude/epic-planck-ll9ar4 https://github.com/arkahakick21-del/open-sites.git /tmp/os && BRANCH=claude/epic-planck-ll9ar4 bash /tmp/os/instagram-bot/deploy/install.sh
```

Скрипт установит ffmpeg, шрифты и Python-зависимости, создаст службу и попросит заполнить `.env`.

## Шаг 5. Заполнить настройки

```bash
nano /opt/insta-assistant/instagram-bot/.env
```

Впишите `TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY` и `OPENAI_API_KEY`.
`ALLOWED_USERS` пока оставьте пустым. Сохраните: Ctrl+O, Enter, Ctrl+X.

Запустите скрипт ещё раз:

```bash
bash /opt/insta-assistant/instagram-bot/deploy/install.sh
```

## Шаг 6. Разрешить доступ специалисту

1. Специалист открывает бота в Telegram и пишет `/start`. Бот ответит её Telegram ID.
2. Впишите этот номер в `ALLOWED_USERS`. Свой ID тоже можно добавить через запятую, чтобы проверять работу.
3. Перезапустите бота:

```bash
systemctl restart insta-bot
```

Проверка: пришлите боту короткое видео с подписью «сделай рилс с субтитрами».

## Шаг 7. Подключить Instagram

По инструкции в [README.md](README.md), раздел «Подключение Instagram».

## Обновление

```bash
bash /opt/insta-assistant/instagram-bot/deploy/install.sh
```

## Если что-то не работает

Логи в реальном времени:

```bash
journalctl -u insta-bot -f
```

| Признак | Что делать |
|---|---|
| Бот молчит | `systemctl status insta-bot`, затем логи |
| «Бот ещё не настроен» | впишите ID в `ALLOWED_USERS` и перезапустите |
| «Файл больше 20 МБ» | ограничение Telegram, см. ниже |
| Не распознаются голосовые | проверьте `OPENAI_API_KEY` |
| Ошибка авторизации Claude | проверьте `ANTHROPIC_API_KEY` и баланс в консоли Anthropic |

## Видео больше 20 МБ

Облачный Telegram отдаёт ботам файлы только до 20 МБ и принимает до 50 МБ.
Пока можно присылать видео обычной отправкой, Telegram сам его сожмёт, или ссылкой на Google Drive.

Чтобы снять ограничение до 2 ГБ, нужен свой сервер Telegram Bot API. Он ставится один раз:

1. На https://my.telegram.org в разделе API development tools получите `api_id` и `api_hash`.
2. Установите Docker и запустите сервер. Подставьте свои значения:

```bash
apt-get install -y docker.io
```

```bash
docker run -d --name tg-bot-api --restart always -p 127.0.0.1:8081:8081 -v /var/lib/telegram-bot-api:/var/lib/telegram-bot-api -e TELEGRAM_API_ID=12345 -e TELEGRAM_API_HASH=abcdef -e TELEGRAM_LOCAL=1 aiogram/telegram-bot-api:latest
```

3. Отвяжите бота от облачного сервера Telegram, подставив токен:

```bash
curl https://api.telegram.org/bot<ТОКЕН>/logOut
```

4. Дайте боту доступ к папке с файлами и разрешите службе её читать:

```bash
chmod -R o+rX /var/lib/telegram-bot-api
```

```bash
sed -i 's#^ReadWritePaths=.*#& /var/lib/telegram-bot-api#' /etc/systemd/system/insta-bot.service && systemctl daemon-reload
```

5. В `.env` впишите `TELEGRAM_API_URL=http://127.0.0.1:8081` и перезапустите бота.

Этот шаг сложнее остальных и проверялся только по документации. Его можно отложить, пока хватает 20 МБ.
