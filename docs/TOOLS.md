# Каталог OSINT-инструментов

Подборка по результатам исследования (сентябрь 2026). Критерии: активность проекта, возможность запуска в Docker, лицензия, пригодность для сценариев S1–S4 (см. [ARCHITECTURE.md §2](ARCHITECTURE.md)).

Условные обозначения: **[Docker]** — готовый официальный образ · **[Св.]** — свой Dockerfile (простой) · Сценарии: **S1** Telegram, **S2** VK, **S3** фото, **S4** ФИО/демография.

---

## 1. Фреймворки «всё сразу»

| Инструмент | Что делает | Docker | Лицензия | Примечание |
|---|---|---|---|---|
| [SpiderFoot](https://github.com/smicallef/spiderfoot) | 200+ модулей сбора по цели (name, email, IP, username…), веб-GUI, API | [Docker](https://hub.docker.com/r/spiderfoot/spiderfoot) | MIT | Ядро Variant C; у нас — коллектор `spiderfoot` для S4. Часть модулей требует API-ключей |
| [Maigret](https://github.com/soxoj/maigret) | 3000+ сайлей по нику, досье (HTML/JSON), парсит профили | **[Docker]** `soxoj/maigret` (+ `:web`) | MIT | Лучший «переходник» ник → аккаунты; пайплайн-библиотека встраивается в наш worker. Для S1, S4 |
| [Sherlock](https://github.com/sherlock-project/sherlock) | 400+ сайтов по нику, быстрый | [Св.] (pip) | MIT | Быстрая проверка перед Maigret |
| [Recon-ng](https://github.com/lanmaster53/recon-ng) | Модульный фреймворк-консоль, витрины данных | [Св.] | GPL-3.0 | Удобен для ручных итераций, в прод-конвейер тяжело вписывается |
| [theHarvester](https://github.com/laramies/theHarvester) | email, поддомены, имена из 30+ источников (поисковики, CT) | [Св.] | **GPL-2.0** | Для S4: email-цепочка по доменам вуза/работы |
| [Blackbird / WhatsMyName](https://github.com/megadose/blackbird) | Проверка нику по списку WhatsMyName (700+) | [Св.] | GPL-3.0 | Альтернатива Sherlock |
| [Awesome-OSINT](https://github.com/edwardtay/awesome-OSINT) | Каталог 360+ инструментов с категориями | — | CC0 | Навигация по экосистеме |

---

## 2. Telegram (S1)

| Инструмент | Что делает | Доступ | Примечание |
|---|---|---|---|
| [Telethon](https://github.com/LonamiWebs/telethon) | MTProto API: профиль, био, фото, common chats, статус | pip, MIT | Нужен свой API ID/Hash и (для common chats) аккаунт — помечаем как *active-метод* |
| [t.me/s/\<channel>](https://t.me/s/channel) | Публичная лента канала без входа | веб | Пассивный скрапинг постов — основной пассивный источник |
| [Telegago](https://bellingcat.gitbook.io/toolkit/more/all-tools/telegago) | Google CSE только по t.me/telegram.me | веб | Индекс Google → обёртка через Custom Search API |
| [TgStat](https://tgstat.com/) / [TgDB](https://www.tgdb.org/) | Статистика/поиск каналов, резолв ID ↔ username, членства | веб/API | Внешние сервисы, соблюдать их ToS |
| [Telegram Phone Number Checker](https://github.com/bellingcat/telegram-phone-number-checker) | Проверка: привязан ли номер к TG (выдаёт username/имя/ID) | pip | Bellingcat; активный метод |
| [Telepathy](https://bellingcat.gitbook.io/toolkit/more/all-tools/telepathy) | Архивация чатов, списки участников, топ-постеры, CSV | CLI | Для анализа каналов, не персона-профилей |
| [telegram-osint (Pyrogram)](https://github.com/yusiqo/telegram-osint) | Профиль + общие группы + сообщения в них | pip | Готовая заготовка для нашего `tg-collector` |

---

## 3. VK (S2, S4)

| Инструмент | Что делает | Доступ | Примечание |
|---|---|---|---|
| **[VK API](https://dev.vk.com/ru/api)** + [vk_api](https://github.com/python273/vk_api) | `users.get` (bdate, city, university, schools), friends, groups, wall, photos, `users.search`, `photos.search` (гео) | REST, Apache-2.0 | **Главный источник S2/S4**: официальный, стабильный, токен приложения |
| [OSINTvk](https://github.com/AdrianGuretto/OSINTvk) | Обёртка: инфо, стена, фото, друзья, группы, гео-пины | pip | Готовая команда действий → наш коллектор |
| [vk-osint-ru](https://github.com/OSINT-mindset/vk-osint-ru) | Подборка живых инструментов (topdb, photo-map, боты) | — | Каталог полезных внешних сервисов |
| [postuf/vkontakte-osint-lib](https://github.com/Postuf/vkontakte-osint-lib) | Scenario-based API для профилей (PHP) | composer | Интересен как референс сценариев |
| [spevktator](https://github.com/MischaU8/spevktator) | Сбор и анализ публичных постов VK-сообществ | pip | Скорее для мониторинга сообществ |
| [photo-map.ru](https://photo-map.ru/) | Поиск фото по местоположению из открытых VK | веб | Для S3+S2: гео-поиск |
| [topdb.ru](https://topdb.ru/username), VKHistoryRobot | Старые копии VK-страниц | веб/бот | Архивные зеркала (внешние сервисы) |

---

## 4. Фото и лица (S3)

| Инструмент | Что делает | Доступ | Примечание |
|---|---|---|---|
| [ExifTool](https://exiftool.org/) | Чтение/запись EXIF: камера, дата, GPS | CLI, **GPL-3.0** | База S3; контейнер стандартный |
| [osint-face-search](https://github.com/jkeylight/osint-face-search) | Агрегация reverse-движков (Yandex/Google/TinEye/Bing) + **локальная** верификация лица (YuNet+SFace), pHash, отчёт | [Св.], Python | **Очень близок к нашему S3-конвейеру** — референс пайплайна и порогов |
| [eye_of_web](https://github.com/MehmetYukselSekeroglu/eye_of_web) | Self-hosted альтернатива face-поисковиков | pip, MIT | Сравнить качество на наших данных |
| [face_recognition](https://github.com/ageitgey/face_recognition) | Детект+encoding (dlib), простой API | pip | Лёгкая верификация 1:1 |
| [InsightFace](https://github.com/deepinsight/insightface) | ArcFace/Antelopev2 — SOTA embedding, ONNX | pip | Для индекса аватарок: FAISS + cosine |
| [search-by-image](https://github.com/dessant/search-by-image) | Браузерное расширение — 30 движков обратного поиска | — | Референс списка движков |
| **Внешние API**: [Yandex](https://yandex.ru/images/) (лучший для лиц), Google Lens, [TinEye](https://tineye.com/), [Baidu](https://image.baidu.com/) | Обратный поиск по картинке | веб/API | Yandex — через Playwright (хрупко) или через SerpAPI-подобные агрегаторы |
| Внешние face-базы: [PimEyes](https://pimeyes.com/), [FaceCheck.ID](https://facecheck.id/), [Search4Faces](https://search4faces.com/) | Поиск по лицу по индексу их дата-центра | веб (часто платно) | Опциональный коннектор; соблюдать ToS; индекс «всего интернета» self-hosted не собираем |
| [Metagoofil](https://github.com/laramies/metagoofil), [Photon](https://github.com/s0md3v/Photon) | Метаданные публичных документов (PDF/DOC), краулинг URL/email | pip | Расширение S4: документы вуза/работы |

---

## 5. ФИО / демография / email (S4)

| Инструмент | Что делает | Доступ | Примечание |
|---|---|---|---|
| **Google/Bing/Yandex dorks** (+ [People-Search](https://github.com/yogsec/People-Search), [Pagodo](https://github.com/xillwill/pagodo), [DorkSearch](https://dorksearch.com/)) | Готовые запросы: `site:vk.com "ФИО"`, `inurl:resume`, `filetype:pdf "ФИО" МГУ` | web/API | Ядро «генератора dorks» в коллекторе; шаблоны храним в YAML |
| [Holehe](https://github.com/megadose/holehe) | Email → на каких сайтах зарегистрирован (120+) | pip, GPL-3.0 | Email → новые аккаунты |
| [GHunt](https://github.com/LubyU/ghunt) | OSINT по аккаунтам Google | pip | Полезен при найденных gmail |
| [PhoneInfoga](https://github.com/sundowndev/phoneinfoga) | Оператор/страна/тип номера (пассивно) | [Docker] | Если всплывёт телефон |
| [socid_extractor](https://github.com/soxoj/socid_extractor) | Извлечение ID/идентификаторов из HTML профилей | pip | Утилита нормализации |
| Публичные каталоги вузов, конференции, дипломы (Google Scholar, ORCID, elibrary) | Прямое подтверждение «учился в X» | веб | Ручной/авто-слой проверки триплета ФИО+город+вуз |
| People-search агрегаторы (Spokeo, Radaris и т.п.) | Готовые досье (преим. США) | веб/платно | ⚠ правовая осторожность; в MVP не включаем |

---

## 6. Вспомогательные

| Инструмент | Роль |
|---|---|
| [Playwright](https://playwright.dev/) | Headless-браузер для reverse image и хрупких веб-источников |
| [OpenSearch](https://opensearch.org/) | Полнотекстовый поиск по собранным фактам (ФИО, био, посты) |
| [MinIO](https://min.io/) | Хранилище фото и HTML-снимков-доказательств |
| [NetworkX](https://networkx.org/) / [Neo4j](https://neo4j.com/) | Граф «друзья/общие группы» (NetworkX на этапе 3, Neo4j — если вырастет) |
| [Prometheus + Grafana](https://prometheus.io/) | Мониторинг квот, задержек, отказов источников |

---

## 7. Как выбрали (сводка решений)

| Потребность | Выбран | Почему не другие |
|---|---|---|
| Ник → аккаунты | **Maigret** (основной) + Sherlock (быстрый) | Maigret: больше сайтов, есть официальный Docker и библиотека |
| Профиль VK | **VK API + vk_api** | Официальный API надёжнее скрапинга; у других инструментов старый/битый API |
| Профиль/посты Telegram | **t.me/s + внешние индексы** пассивно; Telethon — опционально | Пассивность по умолчанию (см. принципы) |
| Фото | **ExifTool + reverse(Playwright) + локальная face-верификация** | Референс — osint-face-search; коммерческие face-базы = внешний коннектор |
| Скан «под ключ» по name/email | **SpiderFoot** как коллектор | Не как ядро (Variant C отклонён) |
| Демография + кандидаты | **dorks-шаблоны + VK users.search + скоринг** | Никакого доступа к закрытым базам |

## 8. Известные риски инструментов

- **Хрупкость скрапинга**: Yandex/Google меняют вёрстку → контрактные тесты-канарейки.
- **Квоты API**: VK (5 req/s), SerpAPI (~100 запросов/мес бесплатно; ~10 запросов = 1 джоба), **Google Custom Search — закрыт для новых клиентов с 2026 г.** (закрытие 01.01.2027), Serper/SearchApi — бесплатные альтернативы — бюджет на кейс.
- **Лицензии с copyleft** (theHarvester GPL-2.0, holehe GPL-3.0, ExifTool GPL-3.0): вызываем как **отдельные процессы/образы**, не встраиваем код в наш MIT-продукт.
- **ToS внешних сервисов** (TGStat, TgDB, PimEyes): только в рамках их правил, без массового обхода лимитов.
