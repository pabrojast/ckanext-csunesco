"""Recipient-language copy, independent of the API request's gettext locale."""

COPY = {
    'en': {
        'subject': 'Verify your UNESCO Citizen Science account',
        'body': 'Welcome to UNESCO Citizen Science!\n\nPlease confirm your email address to activate your account by opening this link:\n\n{url}\n\nThe link expires in {hours} hours. If you did not create this account, you can safely ignore this message.',
        'cta': 'Verify my account',
        'footer': 'This email was sent because your account was created.',
        'brand': 'Citizen Science',
        'support': 'Questions? Reach us at',
    },
    'fr': {
        'subject': 'Vérifiez votre compte de science citoyenne de l’UNESCO',
        'body': 'Bienvenue dans la science citoyenne de l’UNESCO !\n\nVeuillez confirmer votre adresse e-mail pour activer votre compte en ouvrant ce lien :\n\n{url}\n\nCe lien expire dans {hours} heures. Si vous n’avez pas créé ce compte, vous pouvez ignorer ce message.',
        'cta': 'Vérifier mon compte',
        'footer': 'Cet e-mail vous a été envoyé à la suite de la création de votre compte.',
        'brand': 'Science citoyenne',
        'support': 'Des questions ? Contactez-nous à',
    },
    'es': {
        'subject': 'Verifica tu cuenta de Ciencia Ciudadana de la UNESCO',
        'body': '¡Te damos la bienvenida a Ciencia Ciudadana de la UNESCO!\n\nConfirma tu dirección de correo electrónico para activar tu cuenta abriendo este enlace:\n\n{url}\n\nEl enlace caduca en {hours} horas. Si no creaste esta cuenta, puedes ignorar este mensaje.',
        'cta': 'Verificar mi cuenta',
        'footer': 'Recibiste este correo porque se creó tu cuenta.',
        'brand': 'Ciencia Ciudadana',
        'support': '¿Tienes preguntas? Escríbenos a',
    },
    'pt': {
        'subject': 'Verifique a sua conta de Ciência Cidadã da UNESCO',
        'body': 'Bem-vindo à Ciência Cidadã da UNESCO!\n\nConfirme o seu endereço de e-mail para ativar a sua conta, abrindo esta ligação:\n\n{url}\n\nA ligação expira dentro de {hours} horas. Se não criou esta conta, pode ignorar esta mensagem.',
        'cta': 'Verificar a minha conta',
        'footer': 'Este e-mail foi enviado porque a sua conta foi criada.',
        'brand': 'Ciência Cidadã',
        'support': 'Tem dúvidas? Contacte-nos em',
    },
    'uk': {
        'subject': 'Підтвердьте свій обліковий запис громадянської науки ЮНЕСКО',
        'body': 'Ласкаво просимо до громадянської науки ЮНЕСКО!\n\nПідтвердьте свою адресу електронної пошти, щоб активувати обліковий запис, відкривши це посилання:\n\n{url}\n\nТермін дії посилання — {hours} годин. Якщо ви не створювали цей обліковий запис, можете проігнорувати це повідомлення.',
        'cta': 'Підтвердити мій обліковий запис',
        'footer': 'Цей лист надіслано у зв’язку зі створенням вашого облікового запису.',
        'brand': 'Громадянська наука',
        'support': 'Маєте запитання? Напишіть нам на',
    },
    'ar': {
        'subject': 'تحقّق من حسابك في علم المواطن لدى اليونسكو',
        'body': 'مرحباً بك في علم المواطن لدى اليونسكو!\n\nيُرجى تأكيد عنوان بريدك الإلكتروني لتفعيل حسابك بفتح هذا الرابط:\n\n{url}\n\nتنتهي صلاحية الرابط خلال {hours} ساعة. إذا لم تنشئ هذا الحساب، يمكنك تجاهل هذه الرسالة.',
        'cta': 'التحقّق من حسابي',
        'footer': 'أُرسلت هذه الرسالة لأن حسابك قد أُنشئ.',
        'brand': 'علم المواطن',
        'support': 'هل لديك أسئلة؟ تواصل معنا على',
    },
    'quh': {
        'subject': 'UNESCO Llaqta Runa Yachay cuentaykita chiqaqchay',
        'body': 'UNESCO Llaqta Runa Yachayman allin hamusqa kachun!\n\nCuentaykita llamk’achinapaq, kay t’inkiwan correo electronicoykita chiqaqchay:\n\n{url}\n\nKay t’inki {hours} horakama llamk’anqa. Mana qan kay cuentata paqarichirqanki chayqa, kay willayta saqiyta atinki.',
        'cta': 'Cuentayta chiqaqchay',
        'footer': 'Cuentayki paqarisqanrayku kay correo apachisqa karqan.',
        'brand': 'Llaqta Runa Yachay',
        'support': 'Tapuykunayki kanchu? Kayman qillqawayku',
    },
}


def language_code(value):
    code = str(value or '').strip().lower().replace('_', '-').split('-')[0]
    return code if code in COPY else 'en'
