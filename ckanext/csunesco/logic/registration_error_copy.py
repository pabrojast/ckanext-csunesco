"""Public registration errors in all seven Citizen Science languages."""

COPY = {'en': {'required': 'Complete the required fields.',
        'email_invalid': 'Enter a valid email address.',
        'username_invalid': 'Use at least 2 characters: lowercase letters, numbers, hyphens or underscores.',
        'username_taken': 'This username is already in use. Choose another one or sign in.',
        'account_conflict': 'An IHP-WINS account already uses this email. Sign in or reset your password.',
        'password_short': 'Your password must contain at least 8 characters.',
        'date_invalid': 'Enter a valid date of birth that is not in the future.',
        'nationality_invalid': 'Choose a nationality from the list.',
        'gender_invalid': 'Choose a gender option from the list.',
        'terms_required': 'Accept the terms to continue.',
        'captcha_failed': 'The security check failed. Please try again.',
        'too_many_attempts': 'Too many attempts. Wait {{minutes}} minutes before trying again.',
        'registration_disabled': 'Account registration is currently unavailable. Please contact support.',
        'service_unavailable': 'We could not complete your request. Please try again in a few minutes.',
        'password_mismatch': 'The passwords do not match.',
        'motivation_invalid': 'Tell us your motivation in 20 to 500 characters.',
        'portal_unavailable': 'We could not connect to IHP-WINS. Please try again in a few minutes.',
        'network': 'We could not connect. Check your internet connection and try again.',
        'rate_limit_unknown': 'Too many attempts. Please wait a few minutes before trying again.',
        'credentials_invalid': 'We could not verify the credentials. Check your IHP-WINS username or email '
                               'and the password for each account.',
        'session_expired': 'Your session has expired. Sign in again to continue.',
        'access_denied': 'Your account cannot complete this action. Check that you are using the correct '
                         'account.',
        'link_confirmation_required': 'Review the accounts and confirm that both belong to you before '
                                      'continuing.',
        'ihpwins_account_required': 'Link your IHP-WINS account to continue your Project Manager request.',
        'language_invalid': 'Choose a supported language. Other language must contain no more than 64 '
                            'characters.',
        'logo_invalid': 'Choose a PNG, JPG or WebP image no larger than 2 MB.',
        'organization_invalid': 'Check the organization information and complete the highlighted fields.',
        'token_invalid': 'This link is invalid or has already been used. Sign in if you already verified, or '
                         'request a new email.',
        'token_expired': 'This link has expired. Request a new verification email.',
        'verification_required': 'Confirm your email address using the link we send you before signing in. '
                                 'Please also check your spam or junk folder.',
        'identity_conflict': 'This identity or username is already associated with another Toolbox account. '
                             'Contact an administrator to review and consolidate the accounts; neither '
                             'account has been changed.',
        'sign_in': 'Sign in',
        'reset_password': 'Forgot my password?',
        'resend': 'Resend verification email',
        'retry': 'Refresh my registration',
        'contact_support': 'Contact support'},
 'es': {'required': 'Completa los campos obligatorios.',
        'email_invalid': 'Introduce un correo electrónico válido.',
        'username_invalid': 'Usa al menos 2 caracteres: letras minúsculas, números, guiones o guiones bajos.',
        'username_taken': 'Este usuario ya existe. Elige otro nombre o inicia sesión.',
        'account_conflict': 'Ya existe una cuenta IHP-WINS con este correo. Inicia sesión o recupera tu '
                            'contraseña.',
        'password_short': 'La contraseña debe tener al menos 8 caracteres.',
        'date_invalid': 'Introduce una fecha de nacimiento válida que no sea futura.',
        'nationality_invalid': 'Selecciona una nacionalidad de la lista.',
        'gender_invalid': 'Selecciona una opción de género de la lista.',
        'terms_required': 'Acepta los términos para continuar.',
        'captcha_failed': 'La comprobación de seguridad falló. Inténtalo de nuevo.',
        'too_many_attempts': 'Demasiados intentos. Espera {{minutes}} minutos antes de volver a intentarlo.',
        'registration_disabled': 'El registro de cuentas no está disponible. Contacta con soporte.',
        'service_unavailable': 'No pudimos completar la solicitud. Inténtalo de nuevo en unos minutos.',
        'password_mismatch': 'Las contraseñas no coinciden.',
        'motivation_invalid': 'Explica tu motivación en 20 a 500 caracteres.',
        'portal_unavailable': 'No pudimos conectar con IHP-WINS. Inténtalo de nuevo en unos minutos.',
        'network': 'No pudimos conectar. Revisa tu conexión a internet y vuelve a intentarlo.',
        'rate_limit_unknown': 'Demasiados intentos. Espera unos minutos antes de volver a intentarlo.',
        'credentials_invalid': 'No pudimos comprobar las credenciales. Revisa tu usuario o correo IHP-WINS y '
                               'la contraseña de cada cuenta.',
        'session_expired': 'Tu sesión ha caducado. Inicia sesión de nuevo para continuar.',
        'access_denied': 'Tu cuenta no puede completar esta acción. Comprueba que estás usando la cuenta '
                         'correcta.',
        'link_confirmation_required': 'Revisa las cuentas y confirma que ambas te pertenecen antes de '
                                      'continuar.',
        'ihpwins_account_required': 'Vincula tu cuenta IHP-WINS para continuar la solicitud de Project '
                                    'Manager.',
        'language_invalid': 'Selecciona un idioma disponible. Otro idioma debe tener como máximo 64 '
                            'caracteres.',
        'logo_invalid': 'Selecciona una imagen PNG, JPG o WebP de hasta 2 MB.',
        'organization_invalid': 'Revisa la información de la organización y completa los campos señalados.',
        'token_invalid': 'Este enlace no es válido o ya fue utilizado. Inicia sesión si ya verificaste tu '
                         'correo o solicita uno nuevo.',
        'token_expired': 'Este enlace ha caducado. Solicita un nuevo correo de verificación.',
        'verification_required': 'Confirma tu correo con el enlace que te enviaremos antes de iniciar '
                                 'sesión. Revisa también tu carpeta de spam o correo no deseado.',
        'identity_conflict': 'Esta identidad o usuario ya está asociado a otra cuenta Toolbox. Contacta a un '
                             'administrador para revisar y unificar las cuentas; no se ha modificado '
                             'ninguna.',
        'sign_in': 'Ingresar',
        'reset_password': '¿Olvidaste tu contraseña?',
        'resend': 'Reenviar correo de verificación',
        'retry': 'Actualizar mi registro',
        'contact_support': 'Contactar con soporte'},
 'fr': {'required': 'Complétez les champs obligatoires.',
        'email_invalid': 'Saisissez une adresse e-mail valide.',
        'username_invalid': 'Utilisez au moins 2 caractères : lettres minuscules, chiffres, tirets ou traits '
                            'de soulignement.',
        'username_taken': 'Ce nom d’utilisateur est déjà utilisé. Choisissez-en un autre ou connectez-vous.',
        'account_conflict': 'Un compte IHP-WINS utilise déjà cette adresse e-mail. Connectez-vous ou '
                            'réinitialisez votre mot de passe.',
        'password_short': 'Le mot de passe doit contenir au moins 8 caractères.',
        'date_invalid': 'Saisissez une date de naissance valide qui ne soit pas dans le futur.',
        'nationality_invalid': 'Choisissez une nationalité dans la liste.',
        'gender_invalid': 'Choisissez une option de genre dans la liste.',
        'terms_required': 'Acceptez les conditions pour continuer.',
        'captcha_failed': 'La vérification de sécurité a échoué. Réessayez.',
        'too_many_attempts': 'Trop de tentatives. Attendez {{minutes}} minutes avant de réessayer.',
        'registration_disabled': 'L’inscription est indisponible. Contactez l’assistance.',
        'service_unavailable': 'Nous n’avons pas pu terminer votre demande. Réessayez dans quelques minutes.',
        'password_mismatch': 'Les mots de passe ne correspondent pas.',
        'motivation_invalid': 'Expliquez votre motivation en 20 à 500 caractères.',
        'portal_unavailable': 'Nous n’avons pas pu joindre IHP-WINS. Réessayez dans quelques minutes.',
        'network': 'La connexion a échoué. Vérifiez votre connexion internet et réessayez.',
        'rate_limit_unknown': 'Trop de tentatives. Patientez quelques minutes avant de réessayer.',
        'credentials_invalid': 'Nous n’avons pas pu vérifier les identifiants. Vérifiez votre nom '
                               'd’utilisateur ou e-mail IHP-WINS et le mot de passe de chaque compte.',
        'session_expired': 'Votre session a expiré. Reconnectez-vous pour continuer.',
        'access_denied': 'Votre compte ne peut pas effectuer cette action. Vérifiez que vous utilisez le bon '
                         'compte.',
        'link_confirmation_required': 'Vérifiez les comptes et confirmez qu’ils vous appartiennent avant de '
                                      'continuer.',
        'ihpwins_account_required': 'Liez votre compte IHP-WINS pour poursuivre votre demande de Project '
                                    'Manager.',
        'language_invalid': 'Choisissez une langue disponible. Autre langue ne doit pas dépasser 64 '
                            'caractères.',
        'logo_invalid': 'Choisissez une image PNG, JPG ou WebP de 2 Mo maximum.',
        'organization_invalid': 'Vérifiez les informations de l’organisation et complétez les champs '
                                'indiqués.',
        'token_invalid': 'Ce lien est invalide ou a déjà été utilisé. Connectez-vous si votre adresse est '
                         'déjà vérifiée, ou demandez un nouvel e-mail.',
        'token_expired': 'Ce lien a expiré. Demandez un nouvel e-mail de vérification.',
        'verification_required': 'Confirmez votre adresse e-mail avec le lien reçu avant de vous connecter. '
                                 'Pensez également à vérifier votre dossier de courriers indésirables ou de '
                                 'spam.',
        'identity_conflict': 'Cette identité ou cet identifiant est déjà associé à un autre compte Toolbox. '
                             'Contactez un administrateur pour examiner et réunir les comptes ; aucun compte '
                             'n’a été modifié.',
        'sign_in': 'Se connecter',
        'reset_password': 'Mot de passe oublié ?',
        'resend': 'Renvoyer l’e-mail de vérification',
        'retry': 'Actualiser mon inscription',
        'contact_support': 'Contacter l’assistance'},
 'pt': {'required': 'Preencha os campos obrigatórios.',
        'email_invalid': 'Introduza um endereço de e-mail válido.',
        'username_invalid': 'Use pelo menos 2 caracteres: letras minúsculas, números, hífenes ou '
                            'sublinhados.',
        'username_taken': 'Este nome de utilizador já existe. Escolha outro ou inicie sessão.',
        'account_conflict': 'Já existe uma conta IHP-WINS com este e-mail. Inicie sessão ou recupere a '
                            'palavra-passe.',
        'password_short': 'A palavra-passe deve ter pelo menos 8 caracteres.',
        'date_invalid': 'Introduza uma data de nascimento válida que não seja futura.',
        'nationality_invalid': 'Selecione uma nacionalidade da lista.',
        'gender_invalid': 'Selecione uma opção de género da lista.',
        'terms_required': 'Aceite os termos para continuar.',
        'captcha_failed': 'A verificação de segurança falhou. Tente novamente.',
        'too_many_attempts': 'Demasiadas tentativas. Aguarde {{minutes}} minutos antes de tentar novamente.',
        'registration_disabled': 'O registo está indisponível. Contacte o suporte.',
        'service_unavailable': 'Não foi possível concluir o pedido. Tente novamente dentro de alguns '
                               'minutos.',
        'password_mismatch': 'As palavras-passe não coincidem.',
        'motivation_invalid': 'Descreva sua motivação em 20 a 500 caracteres.',
        'portal_unavailable': 'Não foi possível contactar o IHP-WINS. Tente novamente dentro de alguns '
                              'minutos.',
        'network': 'Não foi possível estabelecer a ligação. Verifique a ligação à internet e tente '
                   'novamente.',
        'rate_limit_unknown': 'Demasiadas tentativas. Aguarde alguns minutos antes de tentar novamente.',
        'credentials_invalid': 'Não foi possível verificar as credenciais. Confirme o utilizador ou e-mail '
                               'IHP-WINS e a palavra-passe de cada conta.',
        'session_expired': 'A sessão expirou. Inicie sessão novamente para continuar.',
        'access_denied': 'A sua conta não pode realizar esta ação. Confirme que está a utilizar a conta '
                         'correta.',
        'link_confirmation_required': 'Reveja as contas e confirme que ambas lhe pertencem antes de '
                                      'continuar.',
        'ihpwins_account_required': 'Associe a sua conta IHP-WINS para continuar o pedido de Project '
                                    'Manager.',
        'language_invalid': 'Selecione uma língua disponível. Outra língua deve ter, no máximo, 64 '
                            'caracteres.',
        'logo_invalid': 'Selecione uma imagem PNG, JPG ou WebP com até 2 MB.',
        'organization_invalid': 'Verifique os dados da organização e preencha os campos assinalados.',
        'token_invalid': 'Esta ligação é inválida ou já foi utilizada. Inicie sessão se já verificou o '
                         'e-mail ou peça um novo.',
        'token_expired': 'Esta ligação expirou. Peça um novo e-mail de verificação.',
        'verification_required': 'Confirme seu e-mail pelo link recebido antes de entrar. Verifique também a '
                                 'pasta de spam ou correio indesejado.',
        'identity_conflict': 'Esta identidade ou usuário já está associado a outra conta Toolbox. Contate um '
                             'administrador para revisar e unificar as contas; nenhuma conta foi alterada.',
        'sign_in': 'Entrar',
        'reset_password': 'Esqueceu sua senha?',
        'resend': 'Reenviar e-mail de verificação',
        'retry': 'Atualizar meu cadastro',
        'contact_support': 'Contactar o suporte'},
 'uk': {'required': 'Заповніть обов’язкові поля.',
        'email_invalid': 'Введіть дійсну адресу електронної пошти.',
        'username_invalid': 'Використовуйте щонайменше 2 символи: малі латинські літери, цифри, дефіси або '
                            'підкреслення.',
        'username_taken': 'Це ім’я користувача вже зайняте. Виберіть інше або увійдіть.',
        'account_conflict': 'Обліковий запис IHP-WINS із цією електронною адресою вже існує. Увійдіть або '
                            'відновіть пароль.',
        'password_short': 'Пароль має містити щонайменше 8 символів.',
        'date_invalid': 'Введіть дійсну дату народження, яка не є майбутньою.',
        'nationality_invalid': 'Оберіть громадянство зі списку.',
        'gender_invalid': 'Оберіть варіант статі зі списку.',
        'terms_required': 'Прийміть умови, щоб продовжити.',
        'captcha_failed': 'Перевірка безпеки не вдалася. Спробуйте ще раз.',
        'too_many_attempts': 'Забагато спроб. Зачекайте {{minutes}} хвилин.',
        'registration_disabled': 'Реєстрація недоступна. Зверніться до підтримки.',
        'service_unavailable': 'Не вдалося завершити запит. Спробуйте ще раз за кілька хвилин.',
        'password_mismatch': 'Паролі не збігаються.',
        'motivation_invalid': 'Опишіть мотивацію: від 20 до 500 символів.',
        'portal_unavailable': 'Не вдалося з’єднатися з IHP-WINS. Спробуйте ще раз за кілька хвилин.',
        'network': 'Не вдалося встановити з’єднання. Перевірте підключення до інтернету та повторіть спробу.',
        'rate_limit_unknown': 'Забагато спроб. Зачекайте кілька хвилин перед повторною спробою.',
        'credentials_invalid': 'Не вдалося перевірити облікові дані. Перевірте ім’я користувача або '
                               'електронну адресу IHP-WINS та пароль кожного облікового запису.',
        'session_expired': 'Сеанс завершився. Увійдіть знову, щоб продовжити.',
        'access_denied': 'Ваш обліковий запис не може виконати цю дію. Перевірте, чи використовуєте '
                         'потрібний обліковий запис.',
        'link_confirmation_required': 'Перевірте облікові записи та підтвердьте, що обидва належать вам.',
        'ihpwins_account_required': 'Пов’яжіть обліковий запис IHP-WINS, щоб продовжити заявку Project '
                                    'Manager.',
        'language_invalid': 'Виберіть доступну мову. Інша мова має містити не більше 64 символів.',
        'logo_invalid': 'Виберіть зображення PNG, JPG або WebP розміром до 2 МБ.',
        'organization_invalid': 'Перевірте відомості про організацію та заповніть позначені поля.',
        'token_invalid': 'Посилання недійсне або вже використане. Увійдіть, якщо пошту вже підтверджено, або '
                         'замовте новий лист.',
        'token_expired': 'Термін дії посилання минув. Замовте новий лист підтвердження.',
        'verification_required': 'Підтвердьте електронну адресу за отриманим посиланням перед входом. Також '
                                 'перевірте папку зі спамом або небажаною поштою.',
        'identity_conflict': 'Ця особа або ім’я користувача вже пов’язані з іншим обліковим записом Toolbox. '
                             'Зверніться до адміністратора для перевірки й об’єднання; жоден обліковий запис '
                             'не змінено.',
        'sign_in': 'Увійти',
        'reset_password': 'Забули пароль?',
        'resend': 'Надіслати лист повторно',
        'retry': 'Оновити дані реєстрації',
        'contact_support': 'Звернутися до підтримки'},
 'ar': {'required': 'أكمل الحقول المطلوبة.',
        'email_invalid': 'أدخل عنوان بريد إلكتروني صالحاً.',
        'username_invalid': 'استخدم حرفين على الأقل من الأحرف اللاتينية الصغيرة أو الأرقام أو الشرطات.',
        'username_taken': 'اسم المستخدم مستخدم بالفعل. اختر اسماً آخر أو سجّل الدخول.',
        'account_conflict': 'يوجد حساب IHP-WINS بهذا البريد الإلكتروني. سجّل الدخول أو استعد كلمة المرور.',
        'password_short': 'يجب أن تحتوي كلمة المرور على 8 أحرف على الأقل.',
        'date_invalid': 'أدخل تاريخ ميلاد صالحاً ليس في المستقبل.',
        'nationality_invalid': 'اختر جنسية من القائمة.',
        'gender_invalid': 'اختر خيار الجنس من القائمة.',
        'terms_required': 'اقبل الشروط للمتابعة.',
        'captcha_failed': 'فشل فحص الأمان. حاول مجدداً.',
        'too_many_attempts': 'محاولات كثيرة. انتظر {{minutes}} دقيقة قبل المحاولة مجدداً.',
        'registration_disabled': 'التسجيل غير متاح. تواصل مع الدعم.',
        'service_unavailable': 'تعذّر إكمال الطلب. حاول مرة أخرى بعد بضع دقائق.',
        'password_mismatch': 'كلمتا المرور غير متطابقتين.',
        'motivation_invalid': 'اشرح دافعك في 20 إلى 500 حرف.',
        'portal_unavailable': 'تعذّر الاتصال بـ IHP-WINS. حاول مرة أخرى بعد بضع دقائق.',
        'network': 'تعذّر الاتصال. تحقّق من اتصالك بالإنترنت وحاول مرة أخرى.',
        'rate_limit_unknown': 'محاولات كثيرة جداً. انتظر بضع دقائق قبل المحاولة مجدداً.',
        'credentials_invalid': 'تعذّر التحقق من بيانات الدخول. راجع اسم المستخدم أو البريد الإلكتروني في '
                               'IHP-WINS وكلمة مرور كل حساب.',
        'session_expired': 'انتهت صلاحية جلستك. سجّل الدخول مجدداً للمتابعة.',
        'access_denied': 'لا يمكن لحسابك إكمال هذا الإجراء. تأكّد من استخدام الحساب الصحيح.',
        'link_confirmation_required': 'راجع الحسابين وأكّد أن كليهما يخصّك قبل المتابعة.',
        'ihpwins_account_required': 'اربط حساب IHP-WINS لمتابعة طلب Project Manager.',
        'language_invalid': 'اختر لغة متاحة. يجب ألّا يتجاوز حقل اللغة الأخرى 64 حرفاً.',
        'logo_invalid': 'اختر صورة PNG أو JPG أو WebP لا يزيد حجمها عن 2 ميغابايت.',
        'organization_invalid': 'راجع معلومات المؤسسة وأكمل الحقول المحدّدة.',
        'token_invalid': 'هذا الرابط غير صالح أو تم استخدامه. سجّل الدخول إذا أكّدت بريدك بالفعل، أو اطلب '
                         'رسالة جديدة.',
        'token_expired': 'انتهت صلاحية هذا الرابط. اطلب رسالة تحقّق جديدة.',
        'verification_required': 'أكد بريدك الإلكتروني عبر الرابط المرسل إليك قبل تسجيل الدخول. يُرجى أيضاً '
                                 'التحقق من مجلد الرسائل غير المرغوب فيها أو البريد العشوائي.',
        'identity_conflict': 'هذه الهوية أو اسم المستخدم مرتبط بالفعل بحساب Toolbox آخر. اتصل بمسؤول لمراجعة '
                             'الحسابين ودمجهما؛ لم يتم تغيير أي حساب.',
        'sign_in': 'تسجيل الدخول',
        'reset_password': 'هل نسيت كلمة المرور؟',
        'resend': 'إعادة إرسال رسالة التأكيد',
        'retry': 'تحديث بيانات تسجيلي',
        'contact_support': 'التواصل مع الدعم'},
 'quh': {'required': "Necesitakun imaykunata hunt'achiy.",
         'email_invalid': 'Allin email direcciónta qillqay.',
         'username_invalid': 'Iskay qillqa achka ruway: uchuy qillqakuna, yupaykuna, guionkuna utaq guion '
                             'bajo.',
         'username_taken': 'Kay sutitaqa huk runaña apachkan. Huk sutita akllay utaq yaykuy.',
         'account_conflict': 'Kay correowan IHP-WINS cuentaña kan. Yaykuy utaq contraseñaykita kutichiy.',
         'password_short': 'Contraseñaykiqa pusaq qillqa achka kanan.',
         'date_invalid': "Allin paqarisqa p'unchawta qillqay, mana hamuq p'unchawchu.",
         'nationality_invalid': 'Listamanta huk nación akllay.',
         'gender_invalid': 'Listamanta huk género akllay.',
         'terms_required': 'Aceptay términos nisqata qatiy.',
         'captcha_failed': 'Seguridad qhawayqa pantarqan. Hukmanta ruway.',
         'too_many_attempts': 'Ancha achka ruwaykuna. {{minutes}} minutos suyay hukmanta ruwanaykipaq.',
         'registration_disabled': 'Cuenta ruwayqa kunan mana kanchu. Yanapakuqwan rimay.',
         'service_unavailable': 'Mana mañakuyniykita tukuyta atirqaykuchu. Huk pisi minutumanta wakmanta '
                                'ruway.',
         'password_mismatch': 'Contraseñakuna mana kikinchu.',
         'motivation_invalid': '20manta 500 qillqakama motivacionniykita willaway.',
         'portal_unavailable': 'Mana IHP-WINSman tinkiyta atirqaykuchu. Huk pisi minutumanta wakmanta ruway.',
         'network': 'Mana tinkiyta atirqaykuchu. Internet tinkiykita qhawariy, chaymanta wakmanta ruway.',
         'rate_limit_unknown': 'Ancha kutitaña ruwanki. Huk pisi minututa suyay, chaymanta wakmanta ruway.',
         'credentials_invalid': 'Mana yaykuna willakuykunata chiqaqchayta atirqaykuchu. IHP-WINS sutiykita '
                                'utaq correoykita, sapa cuentap contraseñantawan qhawariy.',
         'session_expired': 'Yaykusqaykip pacha tukukunña. Qatipanaykipaq wakmanta yaykuy.',
         'access_denied': 'Cuentaykiwan kayta mana ruwayta atinkichu. Allin cuentawan yaykusqaykita '
                          'qhawariy.',
         'link_confirmation_required': 'Cuentakunata qhawariy, iskaynin qanpa kasqanta chiqaqchay, chaymanta '
                                       'qatipay.',
         'ihpwins_account_required': 'Project Manager mañakuyniykita qatipanaykipaq IHP-WINS cuentaykita '
                                     'tinkichiy.',
         'language_invalid': 'Kaq simita akllay. Huk simip sutinqa 64 sanampakama kanan.',
         'logo_invalid': 'PNG, JPG utaq WebP siqita akllay, 2 MBkamalla.',
         'organization_invalid': 'Tantakuyp willakuyninta qhawariy, unanchasqa kitikunata huntachiy.',
         'token_invalid': 'Kay t’inki mana allinchu icha llamk’achisqaña. Correo electronicoykita '
                          'chiqaqchankiña chayqa yaykuy, mana chayqa musuq correota mañakuy.',
         'token_expired': 'Kay t’inki tukukapunña. Musuq chiqaqchana correota mañakuy.',
         'verification_required': 'Emailniykita chiqaqchay, apachimusqayku linkwan, manaraq yaykuspa. Spam '
                                  'utaq mana munasqa correokuna waqaychasqatawan qhaway.',
         'identity_conflict': 'Kay runa utaq suti huk Toolbox yupayman t’inkisqaña kachkan. Kamachiqwan '
                              'rimanakuy yupaykunata qhawarinankupaq, huñunankupaq; mana mayqin yupaypas '
                              'tikrasqachu.',
         'sign_in': 'Yaykuy',
         'reset_password': 'Contrasñayta qunqarqani?',
         'resend': 'Chiqaqchay emailta apachimuy',
         'retry': 'Qillqakuyniyta musuqyachiy',
         'contact_support': 'Yanapakuyman qillqay'}}
