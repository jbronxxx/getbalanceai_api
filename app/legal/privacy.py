"""Политика конфиденциальности GetBalance AI (ru, en, uz, kk)."""

from app.legal.types import PageContent
from app.legal.ui import SUPPORT_EMAIL

PRIVACY: dict[str, PageContent] = {
    "ru": {
        "title": "Политика конфиденциальности GetBalance AI",
        "sections": [
            (
                "1. Введение",
                [
                    "GetBalance AI («мы») — мобильное приложение для учёта личных финансов с ИИ-аналитикой. "
                    "Разработчик и оператор данных — Yevgeniy Makarov (Республика Узбекистан, г. Ташкент), "
                    f"контактный email: {SUPPORT_EMAIL}. Эта Политика объясняет, какие данные мы обрабатываем, "
                    "зачем и какие права у вас есть.",
                ],
            ),
            (
                "2. Какие данные мы собираем",
                [
                    [
                        "Учётная запись: адрес электронной почты, имя и (при наличии) фото профиля. "
                        "При регистрации по email хранится только хеш пароля.",
                        "Вход через Google или Apple: идентификатор аккаунта, email, имя и фото, которые "
                        "передаёт провайдер. Пароль от аккаунта Google или Apple нам недоступен.",
                        "Финансовые данные: операции, категории, бюджеты и базовая валюта, которые вы вносите сами.",
                        "Технические данные: IP-адрес, версия приложения и метаданные запросов в серверных "
                        "технических журналах — для безопасности и стабильной работы. Сторонние сервисы "
                        "сбора сбоев в приложении не используются.",
                    ],
                ],
            ),
            (
                "3. Как мы используем данные",
                [
                    [
                        "Создание и управление вашей учётной записью.",
                        "Синхронизация операций и бюджетов между устройствами.",
                        "Формирование персонализированной финансовой аналитики с помощью ИИ.",
                        "Защита от злоупотреблений, поддержка и улучшение сервиса.",
                    ],
                    "Мы не продаём ваши данные и не используем их для рекламы.",
                ],
            ),
            (
                "4. Сторонние сервисы",
                [
                    [
                        "Google (Gemini API) — генерация ИИ-аналитики. Передаются обезличенные записи: даты, "
                        "типы, категории, суммы, валюта и текстовые описания операций; имя, email и "
                        "идентификатор пользователя не передаются. Мы используем коммерческий (платный) тариф "
                        "Gemini API: переданные данные применяются только для формирования ответа и не "
                        "используются Google для обучения моделей ИИ. Не указывайте в описаниях операций "
                        "персональные данные.",
                        "Google Sign-In и Sign in with Apple — вход в аккаунт.",
                        "Google Play и Apple App Store — распространение приложения и оплата платных функций. "
                        "Данные банковской карты обрабатывает магазин; мы их не получаем и не храним.",
                        "Хостинг-провайдер — хранение и обработка данных на серверах (см. раздел 6).",
                    ],
                ],
            ),
            (
                "5. Хранение и удаление",
                [
                    "Данные хранятся, пока существует ваш аккаунт. При удалении аккаунта профиль, операции, "
                    "бюджеты, сессии и кэш аналитики удаляются из базы данных безвозвратно. Резервные копии "
                    "базы данных создаются ежедневно и хранятся не более 7 суток, серверные технические "
                    "журналы — не более 30 дней; по истечении этих сроков удалённые данные исчезают и оттуда.",
                ],
            ),
            (
                "6. Расположение серверов и трансграничная передача",
                [
                    "Серверная инфраструктура GetBalance AI расположена в Европейском Союзе (г. Хельсинки, "
                    "Финляндия), то есть за пределами Республики Узбекистан. Регистрируясь и используя "
                    "Приложение, вы даёте согласие на трансграничную передачу, обработку и хранение ваших "
                    "данных на этих серверах. Если вы не согласны, не используйте Приложение.",
                ],
            ),
            (
                "7. Безопасность",
                [
                    "Мы применяем технические и организационные меры защиты, включая HTTPS и хеширование "
                    "паролей. Однако ни одна передача данных через Интернет не может быть безопасной на 100%.",
                ],
            ),
            (
                "8. Ваши права",
                [
                    "Вы вправе запросить доступ к своим данным, их исправление, копию или удаление. "
                    "Удалить аккаунт и все данные можно в приложении (Профиль → Удалить аккаунт), "
                    "по инструкции на странице «Удаление аккаунта» или по email ниже. "
                    "Мы отвечаем на запросы в течение 30 дней.",
                ],
            ),
            (
                "9. Возраст",
                ["Приложение не предназначено для лиц младше 18 лет, и мы сознательно не собираем их данные."],
            ),
            (
                "10. Применимое право",
                [
                    "Обработка данных осуществляется с учётом законодательства Республики Узбекистан "
                    "о персональных данных.",
                ],
            ),
            (
                "11. Изменения политики",
                [
                    "Мы можем обновлять Политику. Актуальная версия и дата вступления в силу всегда указаны "
                    "на этой странице.",
                ],
            ),
            ("12. Контакты", ["Вопросы и запросы по данным направляйте на email:"]),
        ],
    },
    "en": {
        "title": "GetBalance AI Privacy Policy",
        "sections": [
            (
                "1. Introduction",
                [
                    'GetBalance AI ("we") is a personal finance mobile app with AI-powered insights. The developer '
                    "and data operator is Yevgeniy Makarov (Republic of Uzbekistan, Tashkent), contact email: "
                    f"{SUPPORT_EMAIL}. This Policy explains what data we process, why, and what rights you have.",
                ],
            ),
            (
                "2. Data we collect",
                [
                    [
                        "Account: email address, name and, if available, profile photo. For email sign-up we "
                        "store only a password hash.",
                        "Google or Apple sign-in: account identifier, email, name and photo provided by the "
                        "provider. We never receive your Google or Apple password.",
                        "Financial data: transactions, categories, budgets and base currency that you enter yourself.",
                        "Technical data: IP address, app version and request metadata in server technical logs, "
                        "used for security and stability. The app does not use third-party crash reporting services.",
                    ],
                ],
            ),
            (
                "3. How we use data",
                [
                    [
                        "To create and manage your account.",
                        "To sync transactions and budgets across your devices.",
                        "To generate personalized financial insights using AI.",
                        "To prevent abuse, provide support and improve the service.",
                    ],
                    "We do not sell your data and do not use it for advertising.",
                ],
            ),
            (
                "4. Third-party services",
                [
                    [
                        "Google (Gemini API) generates AI insights. It receives de-identified records: dates, "
                        "types, categories, amounts, currency and free-text descriptions of transactions; your "
                        "name, email and user identifier are not sent. We use the commercial (paid) tier of the "
                        "Gemini API: the data is used only to produce the response and is not used by Google to "
                        "train AI models. Do not put personal data in transaction descriptions.",
                        "Google Sign-In and Sign in with Apple handle account sign-in.",
                        "Google Play and the Apple App Store distribute the app and process payments for paid "
                        "features. Card details are handled by the store; we never receive or store them.",
                        "A hosting provider stores and processes data on servers (see section 6).",
                    ],
                ],
            ),
            (
                "5. Retention and deletion",
                [
                    "Data is kept while your account exists. When you delete your account, your profile, "
                    "transactions, budgets, sessions and analytics cache are permanently erased from the "
                    "database. Database backups are created daily and kept for no more than 7 days, and server "
                    "technical logs for no more than 30 days; after that the deleted data disappears from them too.",
                ],
            ),
            (
                "6. Server location and cross-border transfer",
                [
                    "GetBalance AI server infrastructure is located in the European Union (Helsinki, Finland), "
                    "that is, outside the Republic of Uzbekistan. By registering and using the App you consent "
                    "to the cross-border transfer, processing and storage of your data on these servers. "
                    "If you do not agree, please do not use the App.",
                ],
            ),
            (
                "7. Security",
                [
                    "We apply technical and organizational safeguards, including HTTPS and password hashing. "
                    "However, no transmission over the internet can be guaranteed to be 100% secure.",
                ],
            ),
            (
                "8. Your rights",
                [
                    "You may request access to, correction, a copy or deletion of your data. You can delete "
                    "your account and all data in the app (Profile → Delete account), by following the "
                    "instructions on the Delete account page, or by email below. We respond within 30 days.",
                ],
            ),
            (
                "9. Age",
                ["The app is not intended for people under 18, and we knowingly do not collect their data."],
            ),
            (
                "10. Applicable law",
                ["Data is processed in accordance with the personal data legislation of the Republic of Uzbekistan."],
            ),
            (
                "11. Changes to this policy",
                ["We may update this Policy. The current version and its effective date are always shown on this page."],
            ),
            ("12. Contact", ["Send questions and data requests to:"]),
        ],
    },
    "uz": {
        "title": "GetBalance AI Maxfiylik siyosati",
        "sections": [
            (
                "1. Kirish",
                [
                    'GetBalance AI ("biz") — sun’iy intellekt tahlili bilan shaxsiy moliyani yuritish uchun mobil '
                    "ilova. Ishlab chiquvchi va ma’lumotlar operatori — Yevgeniy Makarov (O‘zbekiston "
                    f"Respublikasi, Toshkent sh.), aloqa uchun email: {SUPPORT_EMAIL}. Ushbu Siyosat qanday "
                    "ma’lumotlarni, nima uchun qayta ishlashimizni va sizning huquqlaringizni tushuntiradi.",
                ],
            ),
            (
                "2. Qanday ma’lumotlarni yig‘amiz",
                [
                    [
                        "Hisob: elektron pochta manzili, ism va (mavjud bo‘lsa) profil surati. Email orqali "
                        "ro‘yxatdan o‘tishda faqat parolning heshi saqlanadi.",
                        "Google yoki Apple orqali kirish: provayder uzatadigan hisob identifikatori, email, ism va "
                        "surat. Google yoki Apple parolingiz bizga ma’lum bo‘lmaydi.",
                        "Moliyaviy ma’lumotlar: o‘zingiz kiritgan operatsiyalar, toifalar, byudjetlar va asosiy valyuta.",
                        "Texnik ma’lumotlar: server texnik jurnallaridagi IP-manzil, ilova versiyasi va so‘rovlar "
                        "metama’lumotlari — xavfsizlik va barqaror ishlash uchun. Ilovada xatoliklarni yig‘uvchi "
                        "uchinchi tomon xizmatlari ishlatilmaydi.",
                    ],
                ],
            ),
            (
                "3. Ma’lumotlardan qanday foydalanamiz",
                [
                    [
                        "Hisobingizni yaratish va boshqarish.",
                        "Operatsiyalar va byudjetlarni qurilmalar o‘rtasida sinxronlash.",
                        "Sun’iy intellekt yordamida shaxsiylashtirilgan moliyaviy tahlillarni taqdim etish.",
                        "Suiiste’molning oldini olish, qo‘llab-quvvatlash va xizmatni yaxshilash.",
                    ],
                    "Biz ma’lumotlaringizni sotmaymiz va reklama uchun ishlatmaymiz.",
                ],
            ),
            (
                "4. Uchinchi tomon xizmatlari",
                [
                    [
                        "Google (Gemini API) — SI tahlillarini yaratish. Shaxsiylashtirilmagan yozuvlar "
                        "uzatiladi: operatsiyalarning sanasi, turi, toifasi, summasi, valyutasi va matnli "
                        "izohlari; ism, email va foydalanuvchi identifikatori uzatilmaydi. Biz Gemini API ning "
                        "tijorat (pullik) tarifidan foydalanamiz: uzatilgan ma’lumotlar faqat javob yaratish "
                        "uchun ishlatiladi va Google tomonidan SI modellarini o‘qitish uchun ishlatilmaydi. "
                        "Operatsiya izohlariga shaxsiy ma’lumotlarni yozmang.",
                        "Google Sign-In va Sign in with Apple — hisobga kirish.",
                        "Google Play va Apple App Store — ilovani tarqatish va pullik funksiyalar uchun to‘lov. "
                        "Bank kartasi ma’lumotlarini do‘kon qayta ishlaydi; biz ularni olmaymiz va saqlamaymiz.",
                        "Xosting-provayder — ma’lumotlarni serverlarda saqlash va qayta ishlash (6-bo‘limga qarang).",
                    ],
                ],
            ),
            (
                "5. Saqlash va o‘chirish",
                [
                    "Ma’lumotlar hisobingiz mavjud ekan saqlanadi. Hisob o‘chirilganda profil, operatsiyalar, "
                    "byudjetlar, seanslar va tahlil keshi ma’lumotlar bazasidan qaytarib bo‘lmaydigan tarzda "
                    "o‘chiriladi. Ma’lumotlar bazasining zaxira nusxalari har kuni yaratiladi va 7 sutkadan "
                    "ko‘p saqlanmaydi, server texnik jurnallari — 30 kundan ko‘p emas; bu muddatlar "
                    "o‘tgach, o‘chirilgan ma’lumotlar u yerdan ham yo‘qoladi.",
                ],
            ),
            (
                "6. Serverlar joylashuvi va transchegaraviy uzatish",
                [
                    "GetBalance AI server infratuzilmasi Yevropa Ittifoqida (Xelsinki sh., Finlyandiya), ya’ni "
                    "O‘zbekiston Respublikasi hududidan tashqarida joylashgan. Ilovada ro‘yxatdan o‘tib va "
                    "undan foydalanib, siz ma’lumotlaringizni ushbu serverlarga transchegaraviy uzatish, "
                    "qayta ishlash va saqlashga rozilik bildirasiz. Rozi bo‘lmasangiz, ilovadan foydalanmang.",
                ],
            ),
            (
                "7. Xavfsizlik",
                [
                    "Biz texnik va tashkiliy himoya choralarini, jumladan HTTPS va parollarni heshlashni "
                    "qo‘llaymiz. Biroq internet orqali hech qanday uzatishning 100% xavfsizligini kafolatlab "
                    "bo‘lmaydi.",
                ],
            ),
            (
                "8. Sizning huquqlaringiz",
                [
                    "Siz ma’lumotlaringizga kirish, ularni tuzatish, nusxasini olish yoki o‘chirishni so‘rashingiz "
                    "mumkin. Hisob va barcha ma’lumotlarni ilovada (Profil → Hisobni o‘chirish), «Hisobni "
                    "o‘chirish» sahifasidagi ko‘rsatma bo‘yicha yoki quyidagi email orqali o‘chirishingiz mumkin. "
                    "So‘rovlarga 30 kun ichida javob beramiz.",
                ],
            ),
            (
                "9. Yosh",
                ["Ilova 18 yoshga to‘lmaganlar uchun mo‘ljallanmagan va biz ularning ma’lumotlarini bila turib yig‘maymiz."],
            ),
            (
                "10. Qo‘llaniladigan huquq",
                [
                    "Ma’lumotlar O‘zbekiston Respublikasining shaxsiy ma’lumotlar to‘g‘risidagi "
                    "qonunchiligi hisobga olingan holda qayta ishlanadi.",
                ],
            ),
            (
                "11. Siyosatdagi o‘zgarishlar",
                ["Siyosatni yangilashimiz mumkin. Joriy versiya va kuchga kirish sanasi doim shu sahifada ko‘rsatiladi."],
            ),
            ("12. Aloqa", ["Savollar va ma’lumotlar bo‘yicha so‘rovlarni quyidagi manzilga yuboring:"]),
        ],
    },
    "kk": {
        "title": "GetBalance AI құпиялылық саясаты",
        "sections": [
            (
                "1. Кіріспе",
                [
                    "GetBalance AI («біз») — жасанды интеллект талдауы бар жеке қаржыны есепке алуға арналған "
                    "мобильді қолданба. Әзірлеуші және деректер операторы — Yevgeniy Makarov (Өзбекстан "
                    f"Республикасы, Ташкент қ.), байланыс email: {SUPPORT_EMAIL}. Бұл Саясат қандай деректерді, "
                    "не үшін өңдейтінімізді және сіздің құқықтарыңызды түсіндіреді.",
                ],
            ),
            (
                "2. Қандай деректерді жинаймыз",
                [
                    [
                        "Аккаунт: электрондық пошта, аты және (болса) профиль суреті. Email арқылы тіркелгенде "
                        "тек парольдің хеші сақталады.",
                        "Google немесе Apple арқылы кіру: провайдер беретін аккаунт идентификаторы, email, аты және "
                        "суреті. Google немесе Apple паролі бізге қолжетімсіз.",
                        "Қаржылық деректер: өзіңіз енгізген операциялар, санаттар, бюджеттер және негізгі валюта.",
                        "Техникалық деректер: серверлік техникалық журналдардағы IP-мекенжай, қолданба нұсқасы "
                        "және сұраныстар метадеректері — қауіпсіздік пен тұрақты жұмыс үшін. Қолданбада ақауларды "
                        "жинайтын үшінші тарап қызметтері пайдаланылмайды.",
                    ],
                ],
            ),
            (
                "3. Деректерді қалай пайдаланамыз",
                [
                    [
                        "Аккаунтыңызды құру және басқару.",
                        "Операциялар мен бюджеттерді құрылғылар арасында синхрондау.",
                        "Жасанды интеллект арқылы жекелендірілген қаржылық талдау ұсыну.",
                        "Теріс пайдаланудан қорғау, қолдау көрсету және қызметті жақсарту.",
                    ],
                    "Біз деректеріңізді сатпаймыз және жарнама үшін пайдаланбаймыз.",
                ],
            ),
            (
                "4. Үшінші тарап қызметтері",
                [
                    [
                        "Google (Gemini API) — ЖИ талдауын жасау. Иесіздендірілген жазбалар беріледі: "
                        "операциялардың күні, түрі, санаты, сомасы, валютасы және мәтіндік сипаттамалары; аты, "
                        "email және пайдаланушы идентификаторы берілмейді. Біз Gemini API-дің коммерциялық "
                        "(ақылы) тарифін қолданамыз: берілген деректер тек жауап жасау үшін пайдаланылады және "
                        "Google оларды ЖИ модельдерін оқыту үшін пайдаланбайды. Операция сипаттамаларына жеке "
                        "деректерді жазбаңыз.",
                        "Google Sign-In және Sign in with Apple — аккаунтқа кіру.",
                        "Google Play және Apple App Store — қолданбаны тарату және ақылы функциялар үшін төлем. "
                        "Банк картасының деректерін дүкен өңдейді; біз оларды алмаймыз және сақтамаймыз.",
                        "Хостинг-провайдер — деректерді серверлерде сақтау және өңдеу (6-бөлімді қараңыз).",
                    ],
                ],
            ),
            (
                "5. Сақтау және жою",
                [
                    "Деректер аккаунтыңыз бар кезде сақталады. Аккаунтты жойғанда профиль, операциялар, "
                    "бюджеттер, сессиялар және талдау кэші деректер қорынан қайтарусыз жойылады. Деректер "
                    "қорының резервтік көшірмелері күн сайын жасалады және 7 тәуліктен артық сақталмайды, "
                    "серверлік техникалық журналдар — 30 күннен артық емес; осы мерзімдер өткен соң жойылған "
                    "деректер олардан да жоғалады.",
                ],
            ),
            (
                "6. Серверлердің орналасуы және трансшекаралық беру",
                [
                    "GetBalance AI серверлік инфрақұрылымы Еуропалық Одақта (Хельсинки қ., Финляндия), яғни "
                    "Өзбекстан Республикасының аумағынан тыс орналасқан. Қолданбада тіркеліп, оны пайдалана "
                    "отырып, сіз деректеріңізді осы серверлерге трансшекаралық беруге, өңдеуге және сақтауға "
                    "келісім бересіз. Келіспесеңіз, қолданбаны пайдаланбаңыз.",
                ],
            ),
            (
                "7. Қауіпсіздік",
                [
                    "Біз техникалық және ұйымдастырушылық қорғау шараларын, оның ішінде HTTPS пен парольдерді "
                    "хештеуді қолданамыз. Алайда интернет арқылы ешбір беру 100% қауіпсіз бола алмайды.",
                ],
            ),
            (
                "8. Сіздің құқықтарыңыз",
                [
                    "Сіз деректеріңізге қол жеткізуді, түзетуді, көшірмесін алуды немесе жоюды сұрай аласыз. "
                    "Аккаунт пен барлық деректерді қолданбада (Профиль → Аккаунтты жою), «Аккаунтты жою» "
                    "бетіндегі нұсқаулық бойынша немесе төмендегі email арқылы жоюға болады. "
                    "Сұраныстарға 30 күн ішінде жауап береміз.",
                ],
            ),
            (
                "9. Жас",
                ["Қолданба 18 жасқа толмағандарға арналмаған, біз олардың деректерін әдейі жинамаймыз."],
            ),
            (
                "10. Қолданылатын құқық",
                ["Деректер Өзбекстан Республикасының дербес деректер туралы заңнамасы ескеріле отырып өңделеді."],
            ),
            (
                "11. Саясаттағы өзгерістер",
                ["Біз Саясатты жаңартуымыз мүмкін. Ағымдағы нұсқа мен күшіне енген күн әрдайым осы бетте көрсетіледі."],
            ),
            ("12. Байланыс", ["Сұрақтар мен деректер бойынша сұраныстарды мына мекенжайға жіберіңіз:"]),
        ],
    },
}
